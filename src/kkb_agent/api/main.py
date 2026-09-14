"""FastAPI transport for readiness and injectable ask execution."""

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

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
from kkb_agent.config import Settings

logger = logging.getLogger(__name__)

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
    if not Path(catalog).exists():
        logger.warning("Catalog %s is absent; /ask will report unavailable", catalog)
        return UnavailableAskRunner()
    return TurnOneAskRunner(catalog)


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
