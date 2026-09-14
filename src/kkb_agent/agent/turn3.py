"""Third published turn: append HPI and revise the active loan finding."""

from dataclasses import dataclass
from datetime import UTC, datetime

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.findings import revise_finding
from kkb_agent.agent.turn1 import BALANCE_KEY, RATE_KEY, _Stage
from kkb_agent.agent.turn2 import CPI_KEY
from kkb_agent.frame import AddColumnParameters, AnalysisFrame, Operation, SpineRange

HPI_KEY = "konut_fiyat_endeksi"
HPI_SERIES_ID = "evds.TP.KFE.TR"
REVISED_FINDING_ID = "f-decline-hpi"
SUPERSEDED_FINDING_ID = "f-decline"
QUESTION = (
    "Bu tabloyu hiç bozmadan, konut fiyat endeksini yeni sütun olarak ekle. "
    "Kredilerin artmamasının nedeni fiyat artışları olabilir mi?"
)


@dataclass(frozen=True)
class TurnThreeResult:
    frame: AnalysisFrame
    plan: tuple[str, ...] = ("add_column(konut_fiyat_endeksi)", "revise_finding(f-decline)")
    caveats: tuple[str, ...] = ()
    planned_by: str = "script"


def _aligned_changes(real, hpi):
    common = [
        index
        for index, (real_value, hpi_value) in enumerate(zip(real.values, hpi.values, strict=True))
        if real_value is not None and hpi_value is not None
    ]
    if len(common) < 2:
        raise ValueError("Turn 3 requires at least two common real-loan and HPI observations")
    start, end = common[0], common[-1]
    real_first, real_last = real.values[start], real.values[end]
    hpi_first, hpi_last = hpi.values[start], hpi.values[end]
    if real_first == 0 or hpi_first == 0:
        raise ValueError("Turn 3 common evidence cannot start from zero")
    return (
        start,
        end,
        hpi_first,
        hpi_last,
        (hpi_last - hpi_first) / abs(hpi_first) * 100,
        (real_last - real_first) / abs(real_first) * 100,
    )


def build_turn3(frame: AnalysisFrame, series_source, *, on_stage=None) -> TurnThreeResult:
    """Apply one existing add operation, then append a finding revision."""
    notify = on_stage or (lambda *args, **kwargs: None)

    def stage(name, tool=None):
        return _Stage(name, notify, tool)

    before = frame
    keys = {column.key for column in frame.columns}
    real_keys = [key for key in keys if key.startswith(f"{BALANCE_KEY}__deflated_by_")]
    if not {BALANCE_KEY, RATE_KEY, CPI_KEY} <= keys or len(real_keys) != 1:
        raise ValueError("Turn 3 requires the completed Turn 2 frame")
    if not any(f.finding_id == SUPERSEDED_FINDING_ID for f in frame.findings) or any(
        f.supersedes == SUPERSEDED_FINDING_ID for f in frame.findings
    ):
        raise ValueError("Turn 3 requires the active Turn 1 decline finding")

    with stage("data_discovery", tool="lakehouse"):
        if series_source.fetch(HPI_SERIES_ID) is None:
            raise ValueError("House Price Index series is unavailable")
    with stage("data_preparation"):
        if not frame.spine.values:
            raise ValueError("Turn 3 requires a non-empty spine")
    with stage("agentic_analytics"):
        operation = Operation(
            operation_id=f"turn3-add-{HPI_KEY}",
            kind="add_column",
            parameters=AddColumnParameters(series_reference=HPI_SERIES_ID, column_key=HPI_KEY),
            timestamp=datetime.now(UTC),
            source_version=frame.version,
            resulting_version=frame.version + 1,
        )
        frame = create_operation_executor(series_source=series_source).execute(frame, operation)
    with stage("analysis"):
        hpi = next(column for column in frame.columns if column.key == HPI_KEY)
        real = next(column for column in frame.columns if column.key == real_keys[0])
        start, end, hpi_first, hpi_last, hpi_change, real_change = _aligned_changes(real, hpi)
        start_date, end_date = frame.spine.values[start], frame.spine.values[end]
        frame = revise_finding(
            frame,
            SUPERSEDED_FINDING_ID,
            revision_id=REVISED_FINDING_ID,
            statement=(
                f"{start_date:%Y-%m} - {end_date:%Y-%m} ortak döneminde Konut Fiyat "
                f"Endeksi {hpi_first:.2f} seviyesinden {hpi_last:.2f} "
                f"seviyesine (%{hpi_change:+.1f}) değişirken reel konut kredisi bakiyesi "
                f"%{real_change:+.1f} değişti. Fiyat ve kredi hareketleri birlikte "
                "görülmektedir; bu karşılaştırma tek başına nedensellik kanıtlamaz."
            ),
            supporting_column_keys=(BALANCE_KEY, real_keys[0], HPI_KEY),
            producing_tool="turn3.arithmetic",
            spine_range=SpineRange(start=start, stop=end + 1),
            caveats=("Konut fiyatları için TCMB TP.KFE.TR seviye endeksi kullanıldı.",),
        )
    with stage("verification"):
        if frame.spine != before.spine or frame.columns[: len(before.columns)] != before.columns:
            raise RuntimeError("Turn 3 changed existing analytical state")
    return TurnThreeResult(frame=frame)
