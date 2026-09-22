"""Answer an arbitrary question: resolve series, let the model plan, compute findings.

The three published turns are a worked example in the brief, not the specification - the
inputs are supplied on demo day (deck p.26). This is the path for everything else, built
from the same generic pieces the turns use: retrieval, the schema-constrained planner, the
executor and the frame contracts. Nothing here knows about housing loans.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

import duckdb

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.date_window import parse_window
from kkb_agent.agent.findings import create_finding
from kkb_agent.agent.planner import PlannerTimeoutError
from kkb_agent.catalog.hybrid import HybridRetrieval, RetrievalTrace
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.frame import AddColumnParameters, AnalysisFrame, Operation, OperationType, Spine

CATALOG_COLUMNS = (
    "series_id, source, raw_label, name_tr, measure_type, unit_raw, unit_normalized, "
    "scale_factor, sector_scope, native_freq, aggregation_rule, province, currency_basis, "
    "nonzero_observations"
)

logger = logging.getLogger(__name__)

MAX_SERIES = 3

WINDOW_START = date(2021, 1, 1)
WINDOW_END = date(2026, 6, 30)


@dataclass(frozen=True)
class Analysis:
    frame: AnalysisFrame
    series_ids: tuple[str, ...]
    planned_by: str = "script"
    plan: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    retrieval_trace: RetrievalTrace | None = None


class AmbiguousSeries(RuntimeError):
    """A safe retrieval refusal, separate from provider or implementation failures."""


class NothingToAnalyse(RuntimeError):
    """The question named nothing we hold. Refusing beats answering about something else."""


def _rows(connection) -> list[dict]:
    return [
        {k: (None if v is None or (isinstance(v, float) and v != v) else v) for k, v in r.items()}
        for r in connection.execute(f"SELECT {CATALOG_COLUMNS} FROM series_catalog")
        .fetchdf()
        .to_dict("records")
    ]


def _periods(connection, series_id: str, start=WINDOW_START, end=WINDOW_END) -> tuple[date, ...]:
    return tuple(
        r[0]
        for r in connection.execute(
            "SELECT period FROM series_observations WHERE series_id = ? "
            "AND period BETWEEN ? AND ? AND value IS NOT NULL ORDER BY period",
            [series_id, start, end],
        ).fetchall()
    )


def _column_key(index: int, row: dict) -> str:
    """A readable key the planner can reference and a reader can recognise."""
    name = str(row.get("name_tr") or row.get("raw_label") or f"seri_{index}")
    slug = "".join(ch if ch.isalnum() else "_" for ch in name.casefold())[:40].strip("_")
    return slug or f"seri_{index}"


def _add(executor, frame, series_id: str, key: str):
    return executor.execute(
        frame,
        Operation(
            operation_id=f"add-{key}",
            kind=OperationType.ADD_COLUMN,
            parameters=AddColumnParameters(series_reference=series_id, column_key=key),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        ),
    )


def _findings(frame: AnalysisFrame, periods) -> AnalysisFrame:
    """One computed statement per column: where it started, where it ended, by how much.

    Every number is read off the frame, so the answer cannot contain a figure the analysis
    did not produce.
    """
    for column in frame.columns:
        present = [(i, v) for i, v in enumerate(column.values) if v is not None]
        if len(present) < 2:
            continue
        (first_i, first), (last_i, last) = present[0], present[-1]
        unit = column.unit.symbol if column.unit and column.unit.symbol else ""
        change = f"%{(last / first - 1) * 100:+.1f}" if first else f"{last - first:+,.2f}"
        frame = create_finding(
            frame,
            finding_id=f"f-{column.key[:28]}",
            statement=(
                f"{column.label}: {periods[first_i]:%Y-%m} - {periods[last_i]:%Y-%m} "
                f"aralığında {first:,.2f} {unit} seviyesinden {last:,.2f} {unit} "
                f"seviyesine geldi ({change})."
            ),
            supporting_column_keys=(column.key,),
            producing_tool="analyze.arithmetic",
            caveats=(f"{len(present)} gözlem üzerinden hesaplandı.",),
        )
    return frame


def analyze(
    question: str,
    catalog,
    *,
    planner=None,
    frame_id: str = "analysis",
    on_stage=None,
    retrieval: HybridRetrieval | None = None,
) -> Analysis:
    """Resolve whatever the question names, plan over it, and compute what happened."""
    start, end = parse_window(question, (WINDOW_START, WINDOW_END))
    started = time.perf_counter()
    notify = on_stage or (lambda *a, **k: None)

    from kkb_agent.agent.turn1 import _Stage

    def stage(name, tool=None):
        return _Stage(name, notify, tool)

    connection = duckdb.connect(str(catalog), read_only=True)
    source = CatalogSeriesSource(catalog)
    try:
        with stage("data_discovery", tool="lakehouse"):
            rows = _rows(connection)
            retrieved = (retrieval or HybridRetrieval()).resolve(question, rows)
            if retrieved.ambiguity is not None:
                raise AmbiguousSeries(retrieved.ambiguity.user_message)
            resolution = retrieved.resolution
            # Whether the question is about our data at all is the router's call, made by
            # the model. A retrieval score cannot decide it: measured across real
            # questions the ranges overlap - "Mevduat toplamı son 5 yılda nasıl değişti?"
            # scores 0.000 while "Bugün hava nasıl?" scores 0.400.
            if not resolution.resolved:
                raise NothingToAnalyse(question)
            chosen, seen = [], set()
            for series_id in resolution.series_ids:
                if series_id not in seen:
                    seen.add(series_id)
                    chosen.append(series_id)
                if len(chosen) == MAX_SERIES:
                    break
            by_id = {r["series_id"]: r for r in rows}

        with stage("data_preparation"):
            periods = _periods(connection, chosen[0], start, end)
            if len(periods) < 2:
                raise NothingToAnalyse(f"{chosen[0]} için yeterli gözlem yok")
            frame = AnalysisFrame(frame_id=frame_id, spine=Spine(values=periods, label="Dönem"))
            available = {_column_key(i, by_id.get(sid, {})): sid for i, sid in enumerate(chosen)}

        with stage("agentic_analytics"):
            frame, planned_by, plan = _plan_and_run(planner, source, frame, available, question)

        with stage("analysis"):
            frame = _findings(frame, periods)

        with stage("verification"):
            caveats = (f"İstenen dönem: {start.isoformat()} – {end.isoformat()}.",) + tuple(
                f"{key}: {by_id[sid].get('sector_scope') or 'kapsam belirtilmedi'}"
                for key, sid in available.items()
                if sid in by_id and by_id[sid].get("sector_scope")
            )
    finally:
        connection.close()
        source.close()

    return Analysis(
        frame=frame,
        series_ids=tuple(chosen),
        planned_by=planned_by,
        plan=plan,
        caveats=caveats,
        elapsed_seconds=time.perf_counter() - started,
        retrieval_trace=retrieved.trace,
    )


def _plan_and_run(planner, source, frame, available: dict[str, str], question: str):
    """Model-chosen operations when MIA is configured, a plain add_column each otherwise."""
    if planner is None:
        executor = create_operation_executor(series_source=source)
        for key, series_id in available.items():
            frame = _add(executor, frame, series_id, key)
        return frame, "script", tuple(f"add_column({k})" for k in available)

    context = "\n".join(
        [
            "Mevcut seriler:",
            *(f"  {sid} -> {key}" for key, sid in available.items()),
            "Sütunlar: yok",
            f"Eksen: {frame.spine.label}",
        ]
    )
    try:
        operations = planner.plan(
            user_intent=question,
            resolved_context=context,
            current_version=frame.version,
            series_references=list(available.values()),
            column_keys=list(available),
        )
        # This bounded initial analysis promises source-series comparisons only.
        # Do not silently rebase/deflate a question that merely asks to show a series.
        if not operations or len(operations) > MAX_SERIES:
            raise ValueError("Unsupported generic plan")
        for operation in operations:
            if operation.kind is not OperationType.ADD_COLUMN or (
                available.get(operation.parameters.column_key)
                != operation.parameters.series_reference
            ):
                raise ValueError("Generic analysis requires the resolved source columns")
        executor = create_operation_executor(series_source=source)
        built = frame
        for operation in operations:
            built = executor.execute(built, operation)
        if built.columns:
            return built, "planner", tuple(str(o.kind) for o in operations)
    except PlannerTimeoutError:
        logger.info("operation_plan_path=model_timeout_fallback")
    except Exception:
        logger.info("operation_plan_path=model_error_fallback")
    return _plan_and_run(None, source, frame, available, question)
