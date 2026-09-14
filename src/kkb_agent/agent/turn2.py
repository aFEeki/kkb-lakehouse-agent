"""The second published turn: add headline CPI, then deflate the loan balance."""

from dataclasses import dataclass
from datetime import UTC, datetime

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.handlers import DEFLATION_CONVENTION
from kkb_agent.frame import AddColumnParameters, AnalysisFrame, DeflateColumnParameters, Operation

CPI_KEY = "tufe"
CPI_SERIES_ID = "evds.TP.GENENDEKS.T1"
QUESTION = "Tabloyu bozmadan, sadece konut kredisi tutarlarını enflasyondan arındırabilir misin?"


@dataclass(frozen=True)
class TurnTwoResult:
    frame: AnalysisFrame
    plan: tuple[str, ...]
    caveats: tuple[str, ...] = ()
    planned_by: str = "script"


def build_turn2(frame: AnalysisFrame, series_source, *, on_stage=None) -> TurnTwoResult:
    """Apply the two existing operations to the supplied immutable Turn 1 snapshot."""
    from kkb_agent.agent.turn1 import BALANCE_KEY, RATE_KEY, _Stage

    notify = on_stage or (lambda *args, **kwargs: None)

    def stage(name, tool=None):
        return _Stage(name, notify, tool)

    keys = {column.key for column in frame.columns}
    if not {BALANCE_KEY, RATE_KEY} <= keys:
        raise ValueError("Turn 2 requires the Turn 1 nominal-loan and interest-rate columns")
    before = frame
    executor = create_operation_executor(series_source=series_source)
    base_date = frame.spine.values[0]

    with stage("data_discovery", tool="lakehouse"):
        if series_source.fetch(CPI_SERIES_ID) is None:
            raise ValueError("Headline CPI series is unavailable")
    with stage("data_preparation"):
        if frame.spine.kind != "date" or not frame.spine.values:
            raise ValueError("Turn 2 requires a non-empty date spine")
    with stage("agentic_analytics"):
        add_cpi = Operation(
            operation_id=f"turn2-add-{CPI_KEY}",
            kind="add_column",
            parameters=AddColumnParameters(series_reference=CPI_SERIES_ID, column_key=CPI_KEY),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        )
        frame = executor.execute(frame, add_cpi)
        deflate = Operation(
            operation_id=f"turn2-deflate-{BALANCE_KEY}",
            kind="deflate_column",
            parameters=DeflateColumnParameters(
                column_key=BALANCE_KEY,
                deflator_column_key=CPI_KEY,
                base_date=base_date,
                convention_reference=DEFLATION_CONVENTION,
            ),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        )
        frame = executor.execute(frame, deflate)
    with stage("analysis"):
        if frame.operations[: len(before.operations)] != before.operations:
            raise RuntimeError("Turn 2 rewrote operation history")
    with stage("verification"):
        if frame.spine != before.spine or frame.columns[: len(before.columns)] != before.columns:
            raise RuntimeError("Turn 2 changed existing analytical state")
    return TurnTwoResult(frame=frame, plan=("add_column(tufe)", f"deflate_column({BALANCE_KEY})"))
