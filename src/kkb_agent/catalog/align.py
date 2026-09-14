"""Put series of different frequencies and different end dates onto one time axis.

Two problems that look separate and are not, because both are answered by the same pass
over the requested series.

**Frequency (SCRUM-26).** Turn 1 needs a weekly interest rate beside a monthly balance.
Collapsing the rate to months is legitimate; expanding the balance to weeks is not. And
*how* a series collapses depends on what it measures: a flow sums over the months in a
quarter, a balance takes the quarter end, a rate averages. Using one rule for all three is
as damaging as the wrong cumulative mode - summing twelve monthly interest rates produces
a number around 500%.

**The ragged edge (SCRUM-30).** Series end on different dates because publication lags
differ: BDDK monthly reaches 2026-06, FinTürk is quarterly to 2026-Q2, EVDS rates run to
last week. A table built to the longest series has a final row where most columns are
blank and one is not, and a reader takes that row as a comparison. DECISIONS #11 settles
this: truncate to the common end, and say so.

Nothing here interpolates, forward-fills or nowcasts. A period with no underlying
observation comes back as None (invariant I7), because a fabricated value is
indistinguishable from a real one once it is on a chart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from kkb_agent.catalog.schema import AggregationRule, Frequency

# Coarseness order. A series may be collapsed into a coarser frequency and never expanded
# into a finer one: a quarterly figure resampled to months invents two observations that
# were never published.
_COARSENESS: dict[str, int] = {
    Frequency.DAILY: 0,
    Frequency.WEEKLY: 1,
    Frequency.MONTHLY: 2,
    Frequency.QUARTERLY: 3,
}

# Anchored to period START, matching how the catalog stores periods: iter_bddk_aylik
# writes 2021-01-01 for January, FinTürk writes 2021-01-01 for Q1.
#
# It has to match. The period-end aliases ("ME", "QE") label January as 2021-01-31, and a
# series that needed no resampling keeps its own 2021-01-01 - so two columns of the same
# month land on different rows and the table cannot be assembled at all.
_RESAMPLE_ALIAS: dict[str, str] = {
    Frequency.DAILY: "D",
    Frequency.WEEKLY: "W",
    Frequency.MONTHLY: "MS",
    Frequency.QUARTERLY: "QS",
}

_HOW: dict[str, str] = {
    AggregationRule.SUM: "sum",
    AggregationRule.LAST: "last",
    AggregationRule.MEAN: "mean",
}


def infer_spine_frequency(values: tuple[date, ...]) -> str:
    """Infer one of the catalog frequencies from an ordered date spine."""
    if len(values) < 2:
        return Frequency.MONTHLY
    gaps = sorted({(b - a).days for a, b in zip(values, values[1:], strict=False)})
    typical = gaps[len(gaps) // 2]
    if typical <= 3:
        return Frequency.DAILY
    if typical <= 10:
        return Frequency.WEEKLY
    if typical <= 45:
        return Frequency.MONTHLY
    return Frequency.QUARTERLY


class UpsampleRefused(ValueError):
    """Raised when a series would have to be expanded into periods it never had."""


@dataclass(frozen=True)
class SeriesSpec:
    """One series to place on the axis, as the catalog describes it."""

    series_id: str
    name: str
    values: dict[date, float | None]
    native_freq: str
    aggregation_rule: str
    unit: str = ""
    scale_factor: float = 1.0
    measure_type: str = ""

    @property
    def coverage_end(self) -> date | None:
        observed = [d for d, v in self.values.items() if v is not None]
        return max(observed) if observed else None


@dataclass(frozen=True)
class AlignedSeries:
    spec: SeriesSpec
    values: dict[date, float | None]
    resampled_from: str | None = None

    @property
    def series_id(self) -> str:
        return self.spec.series_id


@dataclass(frozen=True)
class Alignment:
    """Series on a shared spine, plus what had to be given up to share it."""

    spine: tuple[date, ...]
    series: tuple[AlignedSeries, ...]
    frequency: str
    truncated_to: date | None = None
    truncated_by: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def notice_tr(self) -> str:
        """What the answer has to tell the reader, in Turkish. Empty when nothing was
        given up - an unconditional notice trains people to ignore it."""
        if not self.truncated_by:
            return ""
        names = ", ".join(self.truncated_by)
        return (
            f"Tablo {self.truncated_to:%Y-%m-%d} tarihinde bitiriliyor: "
            f"{names} bu tarihten sonrasını yayımlamıyor. "
            "Daha uzun seriler kesildi, tahmin edilmedi."
        )


def can_collapse(native: str, target: str) -> bool:
    """Whether a series at `native` may be served at `target`."""
    if native not in _COARSENESS or target not in _COARSENESS:
        return False
    return _COARSENESS[native] <= _COARSENESS[target]


def _to_series(values: dict[date, float | None]) -> pd.Series:
    clean = {pd.Timestamp(d): v for d, v in values.items() if v is not None}
    if not clean:
        return pd.Series(dtype="float64")
    return pd.Series(clean).sort_index()


def collapse(spec: SeriesSpec, target: str) -> dict[date, float | None]:
    """Collapse one series to `target`, by the rule its measure type implies.

    The aggregation rule is read off the series, not chosen here. A flow sums, a balance
    takes the period end, a rate averages - and the catalog already recorded which,
    because that is a property of what the number is.
    """
    if not can_collapse(spec.native_freq, target):
        raise UpsampleRefused(
            f"{spec.series_id} is {spec.native_freq} and cannot be served at {target}: "
            "that would invent periods the source never published"
        )

    s = _to_series(spec.values)
    if s.empty:
        return {}
    if spec.native_freq == target:
        return {d.date(): float(v) for d, v in s.items()}

    how = _HOW.get(spec.aggregation_rule, "last")
    resampled = getattr(s.resample(_RESAMPLE_ALIAS[target]), how)()

    # A resample bucket with no underlying observation comes back as NaN from `last` and
    # as 0.0 from `sum`. Zero is the dangerous one: it is a number, and nothing
    # downstream can tell it from an observed zero. Count what actually landed in each
    # bucket and null the empty ones (invariant I7).
    occupancy = s.resample(_RESAMPLE_ALIAS[target]).count()
    out: dict[date, float | None] = {}
    for period, value in resampled.items():
        empty = occupancy.get(period, 0) == 0
        out[period.date()] = None if empty or pd.isna(value) else float(value)
    return out


def align(
    specs: list[SeriesSpec],
    *,
    frequency: str | None = None,
    truncate_to_common_end: bool = True,
) -> Alignment:
    """Put every series on one axis at a frequency all of them can reach.

    `frequency` defaults to the coarsest native frequency present, because that is the
    only one nothing has to be expanded into. Asking for something finer than a member
    series raises rather than silently dropping it: a table quietly missing the column
    that answers the question is worse than an error.
    """
    if not specs:
        return Alignment(spine=(), series=(), frequency=frequency or Frequency.MONTHLY)

    target = frequency or max(specs, key=lambda s: _COARSENESS.get(s.native_freq, 0)).native_freq

    collapsed: list[AlignedSeries] = []
    dropped: list[str] = []
    for spec in specs:
        if not can_collapse(spec.native_freq, target):
            raise UpsampleRefused(
                f"{spec.series_id} is {spec.native_freq}; a {target} axis would require "
                "inventing periods it never published. Ask for a coarser frequency."
            )
        values = collapse(spec, target)
        if not values:
            dropped.append(spec.series_id)
            continue
        collapsed.append(
            AlignedSeries(
                spec=spec,
                values=values,
                resampled_from=spec.native_freq if spec.native_freq != target else None,
            )
        )

    if not collapsed:
        return Alignment(
            spine=(),
            series=(),
            frequency=target,
            dropped=tuple(dropped),
            notes=("no series had an observation to place on the axis",),
        )

    # The ragged edge. Each series ends where it ends; the shared axis can only run to the
    # earliest of those, or the final row compares a published figure against nothing.
    ends = {a.series_id: max(d for d, v in a.values.items() if v is not None) for a in collapsed}
    common_end = min(ends.values())
    latest = max(ends.values())
    truncated_by = tuple(sorted(sid for sid, end in ends.items() if end == common_end))

    periods = sorted({d for a in collapsed for d in a.values})
    if truncate_to_common_end:
        periods = [d for d in periods if d <= common_end]

    spine = tuple(periods)
    served = tuple(
        AlignedSeries(
            spec=a.spec,
            values={d: a.values.get(d) for d in spine},
            resampled_from=a.resampled_from,
        )
        for a in collapsed
    )

    return Alignment(
        spine=spine,
        series=served,
        frequency=target,
        truncated_to=common_end if truncate_to_common_end and common_end < latest else None,
        truncated_by=truncated_by if truncate_to_common_end and common_end < latest else (),
        dropped=tuple(dropped),
    )
