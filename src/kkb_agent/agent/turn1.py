"""SCRUM-44 - the first published question, answered from computed evidence.

    "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"

The trap in this question is that it contains a false premise and a definitional slip, and
an answer that repeats either of them back sounds fluent and is wrong.

**Volume did rise - in lira.** Housing loan balances are tested over the full window and
over the period in which rates fell. Turn 1 reports that nominal comparison from computed
frame values. Inflation adjustment deliberately remains Turn 2.

**The question says hacim - volume - and we do not have volume.** No source publishes
*kullandırılan*, gross new lending. What we have is the balance outstanding, which moves
for reasons other than new lending: repayments, write-offs, revaluation. The answer says
so rather than presenting a balance as if it were lending.

**The rate is a specific rate.** TP.KTF12 is the weekly flow-basis (akım) rate on new
housing loans in TL, a weighted average. It is not the rate on the existing book. Naming it
is the difference between an answer a domain judge accepts and one they do not.

Nothing here asks a model for a number. The findings are arithmetic over the frame, so
every figure is hand-checkable against the gold layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

import duckdb

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.findings import create_finding
from kkb_agent.catalog.retrieval import Concept, build_concepts
from kkb_agent.catalog.series_resolver import SeriesResolution, resolve_series
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.frame import (
    AddColumnParameters,
    AnalysisFrame,
    Operation,
    OperationType,
    Spine,
)
from kkb_agent.frame import (
    Operation as FrameOperation,
)

Frame = AnalysisFrame


class StageCallback(Protocol):
    """Called at each real phase boundary, so a stream reports work rather than theatre.

    `stage` is one of the brief's five; `event` is "start" or "end"; `outcome` is set only
    on an end. A stage that fails ends with "failed" before the exception propagates -
    a stream that simply stops mid-stage tells the reader nothing about where it stopped.
    """

    def __call__(
        self, stage: str, event: str, *, outcome: str | None = None, tool: str | None = None
    ) -> None: ...


class Planner(Protocol):
    """What turn 1 needs from a planner. OperationPlanner satisfies it."""

    def plan(
        self,
        user_intent: str,
        resolved_context: str,
        current_version: int,
        series_references: list[str] | None = ...,
        column_keys: list[str] | None = ...,
        conventions: list[str] | None = ...,
        base_dates: list[str] | None = ...,
    ) -> tuple[FrameOperation, ...]: ...


def resolved_context(available: dict[str, str], frame: Frame, spine_label: str) -> str:
    """What the planner is allowed to know, written out for it.

    Only series the resolver actually found, and only columns the frame actually has. The
    planner emits operations against these names; anything it invents fails schema
    validation or the executor, and both are better than a plausible reference to a series
    that does not exist.
    """
    lines = ["Kullanılabilir seriler (series_reference -> önerilen column_key):"]
    lines += [f"  {series_id} -> {key}" for key, series_id in available.items()]
    lines.append(f"Mevcut sütunlar: {[c.key for c in frame.columns] or 'yok'}")
    lines.append(f"Eksen: {spine_label}")
    return "\n".join(lines)


# The published window. Sixty months exactly, which is what makes "over five years" a
# statement about the data rather than a rounding of it.
WINDOW_START = date(2021, 1, 1)
WINDOW_END = date(2025, 12, 1)

# How many sampled plans to try before falling back. Two is not enough - the model is
# right often enough that a third attempt converts most failures, and each is ~3s.
PLAN_ATTEMPTS = 3

BALANCE_KEY = "konut_kredisi_bakiye"
RATE_KEY = "konut_kredisi_faizi"

# The published question, verbatim. It is what the planner is asked to plan for, so it
# lives beside the series questions rather than only in the runner script.
QUESTION = "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"

BALANCE_QUESTION = "konut kredisi"
RATE_QUESTION = "konut kredisi faizi"

RATE_SERIES_ID = "evds.TP.KTF12"

CATALOG_COLUMNS = (
    "series_id, source, raw_label, name_tr, measure_type, unit_raw, unit_normalized, "
    "scale_factor, sector_scope, native_freq, aggregation_rule, province, currency_basis, "
    "nonzero_observations"
)


@dataclass(frozen=True)
class Evidence:
    """One computed comparison, with the inputs that produced it."""

    label: str
    first: float
    last: float
    change_pct: float | None
    change_abs: float

    def __str__(self) -> str:
        pct = f"{self.change_pct:+.1f}%" if self.change_pct is not None else "—"
        return f"{self.label}: {self.first:,.2f} -> {self.last:,.2f} ({pct})"


@dataclass(frozen=True)
class TurnResult:
    frame: AnalysisFrame
    resolutions: tuple[SeriesResolution, ...]
    evidence: tuple[Evidence, ...]
    caveats: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    planned_by: str = "script"
    plan: tuple[str, ...] = ()

    @property
    def answer_tr(self) -> str:
        return "\n".join(f.statement for f in self.frame.findings)


def _load_catalog(connection: duckdb.DuckDBPyConnection) -> tuple[list[dict], list[Concept]]:
    rows = [
        {k: (None if v is None or (isinstance(v, float) and v != v) else v) for k, v in r.items()}
        for r in connection.execute(f"SELECT {CATALOG_COLUMNS} FROM series_catalog")
        .fetchdf()
        .to_dict("records")
    ]
    return rows, build_concepts(rows)


def _spine_periods(
    connection: duckdb.DuckDBPyConnection, series_id: str, start: date, end: date
) -> tuple[date, ...]:
    return tuple(
        r[0]
        for r in connection.execute(
            "SELECT period FROM series_observations "
            "WHERE series_id = ? AND period BETWEEN ? AND ? AND value IS NOT NULL "
            "ORDER BY period",
            [series_id, start, end],
        ).fetchall()
    )


class TurnOneSpineError(RuntimeError):
    """The selected balance cannot support the published complete monthly window."""


def _expected_months(start: date, end: date) -> tuple[date, ...]:
    months = []
    current = date(start.year, start.month, 1)
    final = date(end.year, end.month, 1)
    while current <= final:
        months.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return tuple(months)


def _validate_published_spine(periods: tuple[date, ...], start: date, end: date) -> None:
    expected = _expected_months(start, end)
    if periods != expected or len(periods) != 60:
        raise TurnOneSpineError(
            "Turn 1 requires exactly 60 contiguous monthly observations from "
            "2021-01 through 2025-12"
        )


def _add(executor, frame: Frame, series_id: str, column_key: str) -> Frame:
    return executor.execute(
        frame,
        Operation(
            operation_id=f"add-{column_key}",
            kind=OperationType.ADD_COLUMN,
            parameters=AddColumnParameters(series_reference=series_id, column_key=column_key),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        ),
    )


class _Stage:
    """Emits start/end around a phase, and "failed" if the phase raises."""

    def __init__(self, name: str, notify, tool: str | None = None):
        self._name, self._notify, self._tool = name, notify, tool

    def __enter__(self):
        self._notify(self._name, "start", tool=self._tool)
        return self

    def __exit__(self, exc_type, exc, tb):
        self._notify(
            self._name, "end", outcome="failed" if exc_type else "succeeded", tool=self._tool
        )
        return False


def _plan_summary(frame: Frame) -> tuple[str, ...]:
    return tuple(f"{op.kind.value}({_op_target(op)})" for op in frame.operations)


def _op_target(operation: FrameOperation) -> str:
    parameters = operation.parameters
    for attribute in ("series_reference", "column_key"):
        value = getattr(parameters, attribute, None)
        if value:
            return str(value)
    return ""


def _unmet(frame: Frame) -> tuple[str, ...]:
    """What the answer needs that this plan did not produce.

    The model is free to plan, but Turn 1 permits only the two source columns. CPI and
    deflation belong to Turn 2 and must not leak into this frame early.
    """
    keys = {c.key for c in frame.columns}
    problems = [key for key in (BALANCE_KEY, RATE_KEY) if key not in keys]
    if keys != {BALANCE_KEY, RATE_KEY}:
        problems.append("Turn 1 dışı sütun")
    if any(operation.kind != OperationType.ADD_COLUMN for operation in frame.operations):
        problems.append("Turn 1 dışı işlem")
    return tuple(problems)


def _scripted_plan(executor, frame: Frame, available: dict[str, str]) -> Frame:
    """The fixed plan. Runs when no planner is supplied, so the demo survives MIA being
    unreachable and the tests never make a network call."""
    for key, series_id in available.items():
        frame = _add(executor, frame, series_id, key)
    return frame


def _planned(
    planner: Planner,
    make_executor,
    frame: Frame,
    available: dict[str, str],
    question: str,
) -> tuple[Frame, str, tuple[str, ...]]:
    """Let the model choose the operations; execute only what validates.

    Every operation still goes through the executor, so the frame contracts apply to a
    model-emitted plan exactly as to a scripted one: the spine cannot move, existing
    columns cannot change, and a reference to a series that does not exist is refused
    rather than silently skipped.

    If the plan is unusable, the scripted plan runs on a *fresh* frame and the result says
    so. Fresh matters: a rejected plan may have applied some of its operations before
    failing, and continuing from a half-built frame would produce a table nobody planned.
    """
    empty = Frame(frame_id=frame.frame_id, spine=frame.spine)
    context = resolved_context(available, empty, str(empty.spine.label or "Dönem"))

    attempts: list[str] = []
    planned: tuple[str, ...] = ()

    # More than one attempt because the plan is sampled, not computed: the same context
    # yields a correct four-step plan on one call and a repeated add_column on the next.
    # Each attempt is executed on a fresh frame, so a plan that fails halfway leaves
    # nothing behind for the next one to build on.
    for attempt in range(PLAN_ATTEMPTS):
        try:
            operations = planner.plan(
                question,
                context,
                empty.version,
                series_references=list(available.values()),
                column_keys=list(available),
                conventions=[],
                base_dates=[],
            )
        except Exception as exc:  # a planner failure is recoverable; a wrong number is not
            attempts.append(f"{attempt + 1}: planner failed ({type(exc).__name__})")
            continue

        planned = tuple(f"{op.kind.value}({_op_target(op)})" for op in operations)
        # A fresh executor per attempt. Its snapshot history is stateful and keyed by
        # (frame_id, version), so replaying version 1 with a different plan on the same
        # executor is a lineage conflict - which is the guard that makes revert_to safe,
        # and which a retry loop would otherwise trip on its second try.
        executor = make_executor()
        built = empty
        try:
            for operation in operations:
                built = executor.execute(built, operation)
        except Exception as exc:
            attempts.append(f"{attempt + 1}: rejected ({type(exc).__name__})")
            continue

        missing = _unmet(built)
        if missing:
            attempts.append(f"{attempt + 1}: plan omitted {', '.join(missing)}")
            continue
        return built, "planner", planned

    return (
        _scripted_plan(make_executor(), empty, available),
        "script (" + "; ".join(attempts) + ")",
        planned,
    )


def find_decline_window(values: tuple[float | None, ...]) -> tuple[int, int] | None:
    """The span from a series' peak to its last observation, if it declined after peaking.

    The question asserts that rates fell. Over 2021-2025 they did not - they roughly
    doubled - so an answer that accepts the premise is wrong, and one that simply rejects
    it is unhelpful, because rates *did* fall from the 2024 peak.

    Finding the peak and measuring from there is what turns the premise into something
    tested rather than assumed. It is also the window the asker almost certainly means.
    """
    observed = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(observed) < 3:
        return None
    peak_index, peak_value = max(observed, key=lambda pair: pair[1])
    last_index, last_value = observed[-1]
    if peak_index >= last_index or last_value >= peak_value:
        return None
    return peak_index, last_index


def _evidence(
    frame: Frame,
    column_key: str,
    label: str,
    scale: bool = True,
    span: tuple[int, int] | None = None,
) -> Evidence | None:
    """First and last observed values of a column, and the change between them.

    Observed, not first and last row: a column with a gap at either end would otherwise
    report a change against a missing value. `span` restricts it to a slice of the spine,
    which is how the same arithmetic serves both the full window and the decline window.
    """
    column = next((c for c in frame.columns if c.key == column_key), None)
    if column is None:
        return None
    factor = column.unit.scale if (scale and column.unit) else 1.0
    values = column.values
    if span is not None:
        lo, hi = span
        values = tuple(v if lo <= i <= hi else None for i, v in enumerate(values))
    observed = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(observed) < 2:
        return None
    first, last = observed[0][1] * factor, observed[-1][1] * factor
    change = None if first == 0 else (last - first) / abs(first) * 100
    return Evidence(label=label, first=first, last=last, change_pct=change, change_abs=last - first)


def build_turn1(
    catalog: Path | str,
    *,
    start: date = WINDOW_START,
    end: date = WINDOW_END,
    frame_id: str = "turn1",
    planner: Planner | None = None,
    on_stage: StageCallback | None = None,
) -> TurnResult:
    """Resolve, assemble and compute the answer to the first published question.

    With a `planner`, the model decides which operations to run and the executor validates
    them; without one, a fixed plan runs. The split is deliberate and is the whole design:

      the model decides WHAT to do      -> planner, schema-constrained
      the code decides IF it is legal   -> executor, invariants, frame contracts
      the code does the arithmetic      -> no number is ever generated

    So a model that emits a nonsense operation fails validation rather than producing a
    wrong number, and the fallback keeps the demo runnable when MIA is unreachable - with
    `planned_by` saying which path ran, because "the agent did this" and "a script did
    this" must never be indistinguishable in a demo.
    """
    started = time.perf_counter()
    connection = duckdb.connect(str(catalog), read_only=True)
    source = CatalogSeriesSource(catalog)

    def make_executor():
        return create_operation_executor(series_source=source)

    executor = make_executor()

    notify = on_stage or (lambda *a, **k: None)

    def stage(name: str, tool: str | None = None):
        return _Stage(name, notify, tool)

    try:
        with stage("data_discovery", tool="lakehouse"):
            rows, concepts = _load_catalog(connection)
            provinces = frozenset(r["province"] for r in rows if r.get("province"))
            balance = resolve_series(
                concepts, BALANCE_QUESTION, candidates=rows, provinces=provinces, limit=8
            )
            rate = resolve_series(
                concepts, RATE_QUESTION, candidates=rows, provinces=provinces, limit=8
            )
            if not balance.resolved or not rate.resolved:
                raise RuntimeError("turn 1 could not resolve both of its series")

        balance_id, rate_id = balance.series_ids[0], rate.series_ids[0]
        if rate_id != RATE_SERIES_ID:
            raise RuntimeError(
                f"turn 1 requires published rate series {RATE_SERIES_ID}, resolved {rate_id}"
            )

        # The balance defines the spine: it is the monthly series, and the rate is weekly
        # and will be collapsed onto it. Building the spine from the rate instead would
        # give a weekly axis the balance cannot fill.
        with stage("data_preparation"):
            periods = _spine_periods(connection, balance_id, start, end)
            _validate_published_spine(periods, start, end)
            frame = Frame(frame_id=frame_id, spine=Spine(values=periods, label="Dönem"))

        available = {
            BALANCE_KEY: balance_id,
            RATE_KEY: rate_id,
        }

        with stage("agentic_analytics"):
            if planner is None:
                frame = _scripted_plan(executor, frame, available)
                planned_by, plan = "script", _plan_summary(frame)
            else:
                frame, planned_by, plan = _planned(
                    planner, make_executor, frame, available, QUESTION
                )

        with stage("analysis"):
            rate_column = next(c for c in frame.columns if c.key == RATE_KEY)
            decline = find_decline_window(rate_column.values)
            evidence = [
                e
                for e in (
                    _evidence(frame, RATE_KEY, "Faiz, tüm dönem (%)", scale=False),
                    _evidence(frame, BALANCE_KEY, "Nominal bakiye, tüm dönem (TL)"),
                )
                if e is not None
            ]
            if decline is not None:
                evidence += [
                    e
                    for e in (
                        _evidence(
                            frame,
                            RATE_KEY,
                            "Faiz, düşüş dönemi (%)",
                            scale=False,
                            span=decline,
                        ),
                        _evidence(
                            frame,
                            BALANCE_KEY,
                            "Nominal bakiye, düşüş dönemi (TL)",
                            span=decline,
                        ),
                    )
                    if e is not None
                ]
            frame = _findings(frame, tuple(evidence), periods, decline, rate_id)

        with stage("verification"):
            caveats = _caveats(frame, balance, rate)
        return TurnResult(
            frame=frame,
            resolutions=(balance, rate),
            evidence=tuple(evidence),
            caveats=caveats,
            elapsed_seconds=time.perf_counter() - started,
            planned_by=planned_by,
            plan=plan,
        )
    finally:
        source.close()
        connection.close()


def _findings(
    frame: Frame,
    evidence: tuple[Evidence, ...],
    periods: tuple[date, ...],
    decline: tuple[int, int] | None,
    rate_series_id: str,
) -> Frame:
    """Turn the computed comparisons into findings. Every number comes from the frame."""
    by = {e.label: e for e in evidence}
    full_window = f"{periods[0]:%Y-%m} - {periods[-1]:%Y-%m}"

    # 1. Test the premise instead of repeating it.
    faiz_all = by.get("Faiz, tüm dönem (%)")
    if faiz_all is not None:
        verdict = "düşmedi, yükseldi" if faiz_all.change_abs > 0 else "düştü"
        frame = create_finding(
            frame,
            finding_id="f-premise",
            statement=(
                f"Sorunun varsaydığının aksine, {full_window} genelinde konut kredisi faizi "
                f"{verdict}: %{faiz_all.first:.2f} -> %{faiz_all.last:.2f} "
                f"({faiz_all.change_abs:+.2f} puan)."
            ),
            supporting_column_keys=(RATE_KEY,),
            producing_tool="turn1.arithmetic",
            caveats=_rate_caveats(rate_series_id),
        )

    # 2. The premise does hold for the window after the peak, so answer that question.
    faiz_dec = by.get("Faiz, düşüş dönemi (%)")
    nominal_dec = by.get("Nominal bakiye, düşüş dönemi (TL)")
    if decline is not None and faiz_dec and nominal_dec:
        window = f"{periods[decline[0]]:%Y-%m} - {periods[decline[1]]:%Y-%m}"
        frame = create_finding(
            frame,
            finding_id="f-decline",
            statement=(
                f"Faizin gerilediği {window} döneminde ({faiz_dec.first:.2f}% -> "
                f"{faiz_dec.last:.2f}%, {faiz_dec.change_abs:+.2f} puan) bakiye nominal "
                f"olarak %{nominal_dec.change_pct:+.0f} değişti. Bu Turn 1 sonucu nominal "
                "bakiyeyi gösterir; enflasyondan arındırma Turn 2 kapsamındadır."
            ),
            supporting_column_keys=(RATE_KEY, BALANCE_KEY),
            producing_tool="turn1.arithmetic",
            caveats=(
                f"Düşüş dönemi hesaplanarak bulundu: faizin zirvesi "
                f"{periods[decline[0]]:%Y-%m}, son gözlem {periods[decline[1]]:%Y-%m}.",
                "Bu aşamada reel/deflate edilmiş kredi kolonu üretilmedi.",
            ),
        )

    # 3. The definitional slip, stated whether or not anyone asks.
    frame = create_finding(
        frame,
        finding_id="f-olcum",
        statement=(
            "Buradaki 'hacim' kullandırılan yeni kredi tutarı değil, dönem sonu kredi "
            "bakiyesidir. Hiçbir kaynak (BDDK Aylık/Haftalık/FinTürk, TCMB EVDS) "
            "kullandırılan konut kredisi tutarını yayımlamıyor; bakiye geri ödeme ve "
            "yeniden değerleme ile de değişir."
        ),
        supporting_column_keys=(BALANCE_KEY,),
        producing_tool="turn1.scope",
        caveats=("DECISIONS #10; 53.792 EVDS serisi arandı, akım serisi bulunamadı.",),
    )
    return frame


def _rate_caveats(rate_series_id: str) -> tuple[str, str]:
    """Describe the series actually resolved; never substitute a hardcoded identifier."""
    return (
        f"Oran {rate_series_id}: yeni kullandırılan TL konut kredilerinin haftalık "
        "ağırlıklı ortalama faizi (akım). Mevcut stokun faizi değildir.",
        "Haftalık seri aylık eksene ortalama alınarak indirgendi.",
    )


def _caveats(frame: Frame, balance: SeriesResolution, rate: SeriesResolution) -> tuple[str, ...]:
    """What the answer has to disclose beyond its findings."""
    out: list[str] = []
    for column in frame.columns:
        if column.missing_count:
            out.append(
                f"{column.key}: {column.missing_count} dönemde gözlem yok; "
                "doldurulmadı, boş bırakıldı."
            )
    for resolution in (balance, rate):
        if resolution.needs_disclosure and resolution.substitution:
            out.append(resolution.substitution.notice_tr())
        for facet in resolution.facets:
            if facet.facet == "sector_scope" and not facet.stated:
                out.append(f"Banka grubu belirtilmedi; {facet.value} kapsamı kullanıldı.")
                break
    return tuple(dict.fromkeys(out))
