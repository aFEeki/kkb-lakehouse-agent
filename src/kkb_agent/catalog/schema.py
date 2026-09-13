"""The series catalog - an index of every series we hold.

Four sources put roughly 4,255 files on disk. Without an index, answering "show me
housing loan interest rates" would mean opening all of them and guessing. One row per
series turns that into a query.

Every field here exists because getting it wrong produces a plausible wrong number, so
none of them are decoration:

    unit + scale        t05 is bin TL where t01-t04 are milyon TL. Mixing them is a 1000x error.
    cumulative_mode     56 BDDK series accumulate. A month-over-month change taken from
                        one of them without de-cumulating is wrong by an order of magnitude.
    measure_type        A housing loan *balance* is not housing lending *extended*.
    sector_scope        BDDK Sektör and EVDS sector aggregates differ by ~3%.
    native_freq         Quarterly FinTürk must never be resampled into months it lacks.
    province            FinTürk is per-province; national and İstanbul are not the same series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from kkb_agent.transform.cumulative import CumulativeMode, StatementKind


class Source(StrEnum):
    BDDK_AYLIK = "bddk_aylik"
    BDDK_HAFTALIK = "bddk_haftalik"
    BDDK_FINTURK = "bddk_finturk"
    EVDS = "evds"


class MeasureType(StrEnum):
    """What a series measures. Governs which operations are legal on it."""

    STOCK = "stock"  # a position at a moment: a loan balance
    FLOW = "flow"  # activity over a period: lending extended, profit earned
    RATE = "rate"  # a percentage: an interest rate
    INDEX = "index"  # a rebased level: CPI
    RATIO = "ratio"  # one quantity over another
    COUNT = "count"  # a number of things: branches, staff
    UNKNOWN = "unknown"  # not yet determined - never guessed


class Frequency(StrEnum):
    DAILY = "D"
    WEEKLY = "W"
    MONTHLY = "M"
    QUARTERLY = "Q"


class AggregationRule(StrEnum):
    """How to collapse a higher frequency into a lower one.

    Coupled to measure_type: a de-cumulated flow sums, a stock takes the period end, a
    rate averages. Using the wrong rule is as damaging as the wrong cumulative mode.
    """

    LAST = "last"
    SUM = "sum"
    MEAN = "mean"


# The default aggregation for each measure type. A flow is the only thing that sums.
DEFAULT_AGGREGATION: dict[MeasureType, AggregationRule] = {
    MeasureType.STOCK: AggregationRule.LAST,
    MeasureType.FLOW: AggregationRule.SUM,
    MeasureType.RATE: AggregationRule.MEAN,
    MeasureType.INDEX: AggregationRule.LAST,
    MeasureType.RATIO: AggregationRule.LAST,
    MeasureType.COUNT: AggregationRule.LAST,
    MeasureType.UNKNOWN: AggregationRule.LAST,
}

# Unit string as published -> (normalised unit, multiplier to reach it).
#
# Not every published figure is money. BDDK's ratio and "other information" tables count
# branches, staff and ATMs, express staff-per-branch in people, and give weighted average
# maturities in days. Those units carry no scale factor, but they still have to be
# recorded: a series whose unit we cannot state is a series we refuse to serve, so
# omitting them is what kept 3,164 real series out of the catalog.
UNIT_SCALE: dict[str, tuple[str, float]] = {
    "bin tl": ("TRY", 1_000.0),
    "milyon tl": ("TRY", 1_000_000.0),
    "milyar tl": ("TRY", 1_000_000_000.0),
    "tl": ("TRY", 1.0),
    "%": ("%", 1.0),
    "adet": ("adet", 1.0),  # branches, banks, ATMs
    "kişi": ("kişi", 1.0),  # people per branch, population per branch
    "gün": ("gün", 1.0),  # weighted average maturity
}

# Units that mean "a number of things". A count is summable across provinces or bank
# groups; a per-capita or per-branch figure in the same table is not, which is why the
# unit alone cannot decide and MeasureType has to be set alongside it.
COUNT_UNITS = frozenset({"adet"})


@dataclass
class SeriesMeta:
    """One row of the catalog."""

    # identity
    series_id: str
    source: Source
    source_ref: str  # "t04#row2" for BDDK, the series code for EVDS
    name_tr: str
    raw_label: str = ""  # what the source called it, preserved through renames

    # semantics - what the number actually is
    measure_type: MeasureType = MeasureType.UNKNOWN
    statement_kind: StatementKind | None = None
    sector_scope: str = ""  # taraf: Sektör, Mevduat, Katılım, ...
    currency_basis: str = ""  # TP | YP | Toplam
    province: str | None = None  # None means national; FinTürk sets this

    # units
    unit_raw: str = ""
    unit_normalized: str = ""
    scale_factor: float = 1.0

    # accumulation and frequency
    cumulative_mode: CumulativeMode = CumulativeMode.AMBIGUOUS
    cumulative_evidence: str = ""
    cumulative_verified_by: str = ""
    native_freq: Frequency = Frequency.MONTHLY
    aggregation_rule: AggregationRule = AggregationRule.LAST

    # coverage and provenance
    coverage_start: date | None = None
    coverage_end: date | None = None
    observations: int = 0
    retrieved_at: datetime | None = None
    source_hash: str = ""
    notes: str = ""

    def is_usable(self) -> tuple[bool, str]:
        """Whether this series may be served.

        A series whose accumulation is unresolved cannot be used: any period-over-period
        figure derived from it would be wrong, and wrong plausibly.
        """
        if self.cumulative_mode is CumulativeMode.AMBIGUOUS:
            return False, "cumulative mode unresolved"
        if self.measure_type is MeasureType.UNKNOWN:
            return False, "measure type not determined"
        if not self.unit_normalized:
            return False, "unit not normalised"
        return True, ""

    def embedding_text(self) -> str:
        """What goes into the vector index.

        The semantics go in alongside the name, because the name alone cannot tell a
        housing loan stock from a housing loan flow.
        """
        parts = [
            self.name_tr,
            f"ölçüm: {self.measure_type}",
            f"birim: {self.unit_raw}",
            f"kapsam: {self.sector_scope}" if self.sector_scope else "",
            f"il: {self.province}" if self.province else "Türkiye geneli",
            f"frekans: {self.native_freq}",
            self.notes,
        ]
        return " | ".join(p for p in parts if p)


CATALOG_TABLE = "series_catalog"

CATALOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {CATALOG_TABLE} (
    series_id              VARCHAR PRIMARY KEY,
    source                 VARCHAR NOT NULL,
    source_ref             VARCHAR NOT NULL,
    name_tr                VARCHAR NOT NULL,
    raw_label              VARCHAR,

    measure_type           VARCHAR NOT NULL,
    statement_kind         VARCHAR,
    sector_scope           VARCHAR,
    currency_basis         VARCHAR,
    province               VARCHAR,          -- NULL means national

    unit_raw               VARCHAR,
    unit_normalized        VARCHAR,
    scale_factor           DOUBLE DEFAULT 1.0,

    cumulative_mode        VARCHAR NOT NULL,
    cumulative_evidence    VARCHAR,
    cumulative_verified_by VARCHAR,
    native_freq            VARCHAR NOT NULL,
    aggregation_rule       VARCHAR NOT NULL,

    coverage_start         DATE,
    coverage_end           DATE,
    observations           INTEGER DEFAULT 0,
    retrieved_at           TIMESTAMP,
    source_hash            VARCHAR,
    notes                  VARCHAR
);
"""

# The observations themselves. Long format, one row per series per period, so sources at
# different frequencies coexist without a ragged wide table.
OBSERVATIONS_TABLE = "series_observations"

OBSERVATIONS_DDL = f"""
CREATE TABLE IF NOT EXISTS {OBSERVATIONS_TABLE} (
    series_id   VARCHAR NOT NULL,
    period      DATE NOT NULL,
    value       DOUBLE,               -- NULL means not observed; never zero-filled
    PRIMARY KEY (series_id, period)
);
"""

INDEXES_DDL = [
    f"CREATE INDEX IF NOT EXISTS idx_catalog_source ON {CATALOG_TABLE}(source);",
    f"CREATE INDEX IF NOT EXISTS idx_catalog_measure ON {CATALOG_TABLE}(measure_type);",
    f"CREATE INDEX IF NOT EXISTS idx_catalog_province ON {CATALOG_TABLE}(province);",
    f"CREATE INDEX IF NOT EXISTS idx_obs_period ON {OBSERVATIONS_TABLE}(period);",
]


def normalise_unit(unit_raw: str | None) -> tuple[str, float]:
    """'Bin TL' -> ('TRY', 1000.0). Unknown units return ('', 1.0) rather than guessing."""
    if not unit_raw:
        return "", 1.0
    return UNIT_SCALE.get(unit_raw.strip().casefold(), ("", 1.0))


def default_aggregation(measure: MeasureType) -> AggregationRule:
    return DEFAULT_AGGREGATION[measure]
