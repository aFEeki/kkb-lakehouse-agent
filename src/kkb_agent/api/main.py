"""FastAPI transport for readiness and injectable ask execution."""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from kkb_agent.agent.planner import OperationPlanner
from kkb_agent.agent.turn1 import CATALOG_COLUMNS as TURN1_CATALOG_COLUMNS
from kkb_agent.agent.turn1 import RATE_SERIES_ID, WINDOW_END, WINDOW_START
from kkb_agent.api.contracts import (
    STREAM_EVENT_ADAPTER,
    AskRequest,
    CompletionEvent,
    CompletionPayload,
    ErrorEvent,
    ErrorPayload,
    StageEndEvent,
    StageEndPayload,
    StageStartEvent,
)
from kkb_agent.api.runner import AskRunner, UnavailableAskRunner
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.duckdb_store import DuckDBStore
from kkb_agent.catalog.lance_store import LanceStore
from kkb_agent.catalog.series_source import CATALOG_COLUMNS as SERIES_SOURCE_COLUMNS
from kkb_agent.config import Settings
from kkb_agent.llm.client import MIAClient

logger = logging.getLogger(__name__)

# A key that is absent, or still the placeholder the example env ships with, means MIA was
# never configured here. Constructing a planner against one costs three failed attempts per
# request before the fallback runs; recognising it up front costs nothing.
_PLACEHOLDER_KEY = "API_KEYINIZ"

# What turn 1 reads, and the column lists it reads with. The two catalog lists overlap but
# neither contains the other: turn 1 needs raw_label and nonzero_observations for
# retrieval, CatalogSeriesSource needs source_hash, retrieved_at and cumulative_mode for
# lineage. A catalog satisfying only one of them fails in the stage that uses the other.
_TURN1_PROBES = (
    ("series_catalog", TURN1_CATALOG_COLUMNS),
    ("series_catalog", SERIES_SOURCE_COLUMNS),
    ("series_observations", "series_id, period, value"),
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_PUBLIC_EXECUTION_ERROR = "İstek işlenirken beklenmeyen bir hata oluştu."


def create_app(
    settings: Settings | None = None,
    *,
    ask_runner: AskRunner | None = None,
) -> FastAPI:
    config_for_runner = settings if settings is not None else Settings()
    runner = ask_runner if ask_runner is not None else _default_runner(config_for_runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings()
        app.state.stores = {
            "duckdb": DuckDBStore(config.duckdb_path),
            "lancedb": LanceStore(config.lancedb_path),
        }
        yield

    application = FastAPI(title="KKB Lakehouse Agent", lifespan=lifespan)
    config = settings if settings is not None else Settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type"],
    )

    @application.get("/health")
    def health():
        components = {"application": "ok"}
        for name, store in application.state.stores.items():
            try:
                components[name] = "ok" if store.check() else "error"
            except Exception:
                logger.exception("Local readiness check failed: %s", name)
                components[name] = "error"
        ready = all(value == "ok" for value in components.values())
        return JSONResponse(
            {"status": "ok" if ready else "degraded", "components": components},
            status_code=200 if ready else 503,
        )

    @application.post("/ask")
    def ask(request: AskRequest):
        return StreamingResponse(
            _stream_ask(runner, request),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    return application


def _default_runner(config: Settings) -> AskRunner:
    """Turn 1 when the catalog is present, the explicit placeholder when it is not.

    A missing catalog is a deployment fact, not a bug, and it must not look like one: the
    placeholder produces a clean error event and a failed completion, where a runner built
    against an absent file would raise per request and log a stack trace every time.
    """
    catalog = config.duckdb_path
    if not _turn1_catalog_ready(catalog):
        logger.warning("Turn 1 catalog is not ready; /ask will report unavailable")
        return UnavailableAskRunner()
    return TurnOneAskRunner(catalog, planner=_planner_for(config))


def _planner_for(config: Settings) -> OperationPlanner | None:
    """The model planner when MIA is configured, None when it is not.

    Nothing constructed one before, so every request served through the API ran the
    scripted fallback: the planner existed and was tested, and the deployed system still
    demonstrated a script rather than an agent. The answer said so plainly - `Plan: script`
    on every response - which is the right disclosure and the wrong behaviour.

    Absent credentials stay a deployment fact rather than an error. Turn 1 already falls
    back to the scripted plan when the model is unreachable or returns a plan that does not
    meet the turn's preconditions, so an unconfigured environment degrades to exactly the
    previous behaviour instead of failing, and CI keeps running offline.
    """
    key = config.mia_api_key.get_secret_value().strip()
    if not key or key == _PLACEHOLDER_KEY:
        logger.warning("MIA is not configured; /ask will plan with the scripted fallback")
        return None
    return OperationPlanner(MIAClient(config))


def _turn1_catalog_ready(catalog: Path | str) -> bool:
    """Whether turn 1 will reach its data, not merely whether a database exists.

    The check this replaces asked only whether the two tables were present and held a row.
    A catalog built one commit before `nonzero_observations` was added satisfies that and
    then fails inside the first stage of every request: the stream opens a stage, reports a
    tool, and dies. On a demo that reads as a broken product, where an honest "unavailable"
    reads as a deployment nobody has fed yet - so a readiness check weaker than what the
    turn requires is worse than having none.

    Each column requirement is probed by running the real query with LIMIT 0. Binding
    happens before execution, so a missing column raises here rather than mid-stream, and
    no row is read. The column lists are imported from the modules that query with them:
    keeping a copy here is exactly how this check and turn 1 drifted apart.
    """
    if not Path(catalog).is_file():
        return False
    try:
        connection = duckdb.connect(str(catalog), read_only=True)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
            }
            if not {"series_catalog", "series_observations"} <= tables:
                logger.warning("Catalog is missing tables turn 1 reads: %s", tables)
                return False

            for table, columns in _TURN1_PROBES:
                connection.execute(f"SELECT {columns} FROM {table} LIMIT 0").fetchall()

            if any(
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
                for table in ("series_catalog", "series_observations")
            ):
                logger.warning("Catalog tables are present but empty")
                return False

            # Turn 1 resolves its rate series by retrieval and then requires the result to
            # be this one, so a catalog without it fails the turn no matter what else it
            # holds. Presence here is necessary, not sufficient - retrieval could still
            # land elsewhere - but it is the part that can be settled without running the
            # 872-concept resolution that turn 1 runs anyway.
            covered = connection.execute(
                "SELECT 1 FROM series_observations WHERE series_id = ? "
                "AND period BETWEEN ? AND ? AND value IS NOT NULL LIMIT 1",
                [RATE_SERIES_ID, WINDOW_START, WINDOW_END],
            ).fetchone()
            if covered is None:
                logger.warning(
                    "Catalog has no %s observations in %s..%s; turn 1 cannot be answered",
                    RATE_SERIES_ID,
                    WINDOW_START,
                    WINDOW_END,
                )
                return False
            return True
        finally:
            connection.close()
    except Exception:
        logger.warning("Turn 1 catalog readiness check failed", exc_info=True)
        return False


async def _stream_ask(runner: AskRunner, request: AskRequest):
    last_event = None
    open_stage = None
    saw_error = False

    try:
        async for produced in runner.run(request):
            event = STREAM_EVENT_ADAPTER.validate_python(produced)
            yield _encode_sse(event)
            last_event = event
            if isinstance(event, StageStartEvent):
                open_stage = event.payload.stage
            elif isinstance(event, StageEndEvent):
                open_stage = None
            elif isinstance(event, ErrorEvent):
                saw_error = True
            elif isinstance(event, CompletionEvent):
                return
    except Exception:
        logger.exception("Ask runner failed for analysis %s", request.analysis_id)

    sequence = last_event.sequence + 1 if last_event is not None else 0
    frame_version = last_event.frame_version if last_event is not None else request.version
    occurred_at = _next_timestamp(last_event)

    if not saw_error:
        error = ErrorEvent(
            event_id=f"transport-error-{sequence}",
            analysis_id=request.analysis_id,
            sequence=sequence,
            frame_version=frame_version,
            occurred_at=occurred_at,
            type="error",
            payload=ErrorPayload(
                code="ASK_EXECUTION_FAILED",
                user_message=_PUBLIC_EXECUTION_ERROR,
                retryable=True,
            ),
        )
        yield _encode_sse(error)
        last_event = error
        sequence += 1

    if open_stage is not None:
        stage_end = StageEndEvent(
            event_id=f"transport-stage-end-{sequence}",
            analysis_id=request.analysis_id,
            sequence=sequence,
            frame_version=frame_version,
            occurred_at=_next_timestamp(last_event),
            type="stage_end",
            payload=StageEndPayload(stage=open_stage, outcome="failed"),
        )
        yield _encode_sse(stage_end)
        last_event = stage_end
        sequence += 1

    completion = CompletionEvent(
        event_id=f"transport-completion-{sequence}",
        analysis_id=request.analysis_id,
        sequence=sequence,
        frame_version=frame_version,
        occurred_at=_next_timestamp(last_event),
        type="completion",
        payload=CompletionPayload(outcome="failed"),
    )
    yield _encode_sse(completion)


def _next_timestamp(previous):
    now = datetime.now(UTC)
    if previous is not None and previous.occurred_at > now:
        return previous.occurred_at
    return now


def _encode_sse(event) -> str:
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


app = create_app()
