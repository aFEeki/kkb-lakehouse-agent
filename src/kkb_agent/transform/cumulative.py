"""Cumulative-mode classification and de-cumulation.

The organizers warned that some BDDK data accumulates from the start of publication and
some accumulates annually, and that disaggregating it correctly matters. This module
decides which mode a series is in, and undoes the accumulation.

Two traps shape the design.

**Monotonicity is not evidence.** Over 2021-2026 Turkish lira series rose in almost every
month regardless of what they measured, because inflation peaked near 85%. "It only goes
up" says nothing about whether a series is cumulative.

**The January reset is necessary but not sufficient.** It separates year-to-date
accumulation from everything else, but it cannot tell an ordinary stock apart from one
that accumulates since inception - both are smooth and neither resets. Source definition
has to settle that case, so this module reports the ambiguity rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import pandas as pd


class CumulativeMode(StrEnum):
    NONE = "none"
    YTD = "ytd"
    INCEPTION = "inception"
    AMBIGUOUS = "ambiguous"  # patterns cannot separate; needs the source definition


class StatementKind(StrEnum):
    """Which financial statement a BDDK table represents.

    This is the source definition the pattern checks cannot supply, and it settles the
    ambiguous cases on accounting grounds rather than by eye:

    An **income statement** measures activity over a period, so BDDK reports it
    year-to-date and it resets each January.

    A **balance sheet** measures a position at a moment. It never accumulates - a loan
    balance is what is outstanding today, not the sum of every loan ever written.

    **Off-balance-sheet** commitments are positions too: guarantees outstanding, not
    guarantees ever issued.

    **Ratios** inherit from whatever they are built on.
    """

    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    OFF_BALANCE_SHEET = "off_balance_sheet"
    RATIO = "ratio"


# BDDK Aylık Bülten table -> statement kind. Derived from what each table reports.
BDDK_AYLIK_STATEMENT: dict[int, StatementKind] = {
    1: StatementKind.BALANCE_SHEET,  # Bilanço
    2: StatementKind.INCOME_STATEMENT,  # Kar Zarar
    3: StatementKind.BALANCE_SHEET,  # Krediler
    4: StatementKind.BALANCE_SHEET,  # Tüketici Kredileri
    5: StatementKind.BALANCE_SHEET,  # Sektörel Kredi Dağılımı
    6: StatementKind.BALANCE_SHEET,  # KOBİ Kredileri
    7: StatementKind.BALANCE_SHEET,  # Sendikasyon Seküritizasyon
    8: StatementKind.BALANCE_SHEET,  # Menkul Kıymetler
    9: StatementKind.BALANCE_SHEET,  # Mevduat Türler İtibarıyla
    10: StatementKind.BALANCE_SHEET,  # Mevduat Vade İtibarıyla
    11: StatementKind.BALANCE_SHEET,  # Likidite Durumu
    12: StatementKind.BALANCE_SHEET,  # Sermaye Yeterliliği
    13: StatementKind.BALANCE_SHEET,  # Yabancı Para Pozisyonu
    14: StatementKind.OFF_BALANCE_SHEET,  # Bilanço Dışı İşlemler
    15: StatementKind.RATIO,  # Rasyolar
    16: StatementKind.RATIO,  # Diğer Bilgiler
    17: StatementKind.RATIO,  # Yurt Dışı Şube Rasyoları
}

# Balance-sheet tables carrying a small number of income-statement lines. BDDK shows
# period profit on the balance sheet, and it accumulates like the P&L it comes from.
INCOME_LINES_ON_BALANCE_SHEET = {"dönem karı (zararı)", "dönem net karı (zararı)"}


def resolve_by_statement(
    table_no: int,
    label: str,
    pattern_mode: CumulativeMode,
    *,
    statements: dict[int, StatementKind] | None = None,
) -> tuple[CumulativeMode, str]:
    """Settle an ambiguous classification using the table's statement kind.

    Returns (mode, reason). Only AMBIGUOUS results are resolved - a confident pattern
    result is left alone, because the data disagreeing with the accounting is itself
    information and should not be silently overwritten.
    """
    if pattern_mode is not CumulativeMode.AMBIGUOUS:
        return pattern_mode, "settled by pattern; statement kind not consulted"

    kind = (statements or BDDK_AYLIK_STATEMENT).get(table_no)
    if kind is None:
        return pattern_mode, f"no statement kind recorded for table {table_no}"

    if label.strip().casefold() in INCOME_LINES_ON_BALANCE_SHEET:
        return CumulativeMode.YTD, "income-statement line reported on the balance sheet"

    if kind is StatementKind.INCOME_STATEMENT:
        return CumulativeMode.YTD, (
            "income statement: measures activity over a period, reported year-to-date "
            "and reset each January"
        )
    if kind in (StatementKind.BALANCE_SHEET, StatementKind.OFF_BALANCE_SHEET):
        return CumulativeMode.NONE, (
            f"{kind.replace('_', ' ')}: a position at a point in time, which does not "
            "accumulate - the balance outstanding, not the sum of everything ever written"
        )
    return CumulativeMode.NONE, "ratio of positions; not an accumulating quantity"


@dataclass
class Evidence:
    """Why a series was classified the way it was, so a reviewer can disagree."""

    mode: CumulativeMode
    january_resets: int = 0
    january_opportunities: int = 0
    within_year_monotone_years: int = 0
    years_observed: int = 0
    globally_monotone: bool = False
    negative_periods: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def reset_ratio(self) -> float:
        return (
            self.january_resets / self.january_opportunities if self.january_opportunities else 0.0
        )

    def summary(self) -> str:
        return (
            f"{self.mode}: january resets {self.january_resets}/{self.january_opportunities}, "
            f"within-year monotone {self.within_year_monotone_years}/{self.years_observed}, "
            f"globally monotone {self.globally_monotone}"
            + ("; " + "; ".join(self.notes) if self.notes else "")
        )


def classify(
    s: pd.Series,
    *,
    reset_drop: float = 0.5,
    min_reset_ratio: float = 0.6,
) -> Evidence:
    """Classify a monthly series by its numerical behaviour.

    `reset_drop` - January counts as a reset when it falls to at most this fraction of the
    preceding December. A genuine year-to-date series restarts from near zero, so the
    January value is normally far below December's.

    Returns AMBIGUOUS rather than guessing when a smooth non-resetting series could be
    either an ordinary stock or inception-cumulative.
    """
    s = s.dropna().sort_index()
    if len(s) < 13:
        ev = Evidence(mode=CumulativeMode.AMBIGUOUS)
        ev.notes.append(f"only {len(s)} observations; too short to classify")
        return ev

    years = sorted({d.year for d in s.index})
    resets = opportunities = 0
    monotone_years = 0

    for y in years:
        year_vals = s[[d.year == y for d in s.index]]
        if len(year_vals) >= 2 and year_vals.is_monotonic_increasing:
            monotone_years += 1
        jan = s[[d.year == y and d.month == 1 for d in s.index]]
        dec_prev = s[[d.year == y - 1 and d.month == 12 for d in s.index]]
        if len(jan) and len(dec_prev):
            opportunities += 1
            prev = float(dec_prev.iloc[0])
            if prev > 0 and float(jan.iloc[0]) <= prev * reset_drop:
                resets += 1

    ev = Evidence(
        mode=CumulativeMode.NONE,
        january_resets=resets,
        january_opportunities=opportunities,
        within_year_monotone_years=monotone_years,
        years_observed=len(years),
        globally_monotone=bool(s.is_monotonic_increasing),
        negative_periods=int((s < 0).sum()),
    )

    if ev.reset_ratio >= min_reset_ratio and monotone_years >= max(1, len(years) - 2):
        ev.mode = CumulativeMode.YTD
        ev.notes.append("resets each January and accumulates within the year")
        return ev

    if ev.reset_ratio >= min_reset_ratio:
        ev.mode = CumulativeMode.AMBIGUOUS
        ev.notes.append("January resets present but the series does not accumulate within years")
        return ev

    if ev.globally_monotone:
        ev.mode = CumulativeMode.AMBIGUOUS
        ev.notes.append(
            "never resets and never falls - an ordinary nominal stock and an "
            "inception-cumulative series look identical here. Monotonicity is not evidence "
            "under 2021-2026 Turkish inflation; the source definition must decide"
        )
        return ev

    ev.mode = CumulativeMode.NONE
    ev.notes.append("falls in some periods, so it is not accumulating")
    return ev


def decumulate(s: pd.Series, mode: CumulativeMode) -> pd.Series:
    """Convert an accumulating series to period values.

    Where the prior observation needed for a difference is missing, the period is left
    null rather than fabricated.
    """
    s = s.sort_index()
    if mode is CumulativeMode.NONE:
        return s.copy()
    if mode is CumulativeMode.INCEPTION:
        return s.diff()
    if mode is CumulativeMode.YTD:
        out = s.copy()
        for y in sorted({d.year for d in s.index}):
            mask = [d.year == y for d in s.index]
            year_vals = s[mask]
            if not len(year_vals):
                continue
            diffs = year_vals.diff()
            # The year's first observation has no prior period, so it IS that period's
            # value. Only that one position is filled. A gap later in the year leaves
            # both it and the following period null, because neither is known — do not
            # use fillna here, which would fabricate every missing month.
            diffs.iloc[0] = year_vals.iloc[0]
            out[mask] = diffs
        return out
    raise ValueError(f"cannot de-cumulate mode {mode!r} - resolve the ambiguity first")


def ytd_closure_holds(
    original: pd.Series, decumulated: pd.Series, year: int, *, tol: float = 0.01
) -> bool:
    """Invariant I1: de-cumulated months of a year sum to that year's December total.

    The strongest evidence available that a de-cumulation is right, and it needs no
    external reference.
    """
    dec = original[[d.year == year and d.month == 12 for d in original.index]]
    parts = decumulated[[d.year == year for d in decumulated.index]].dropna()
    if not len(dec) or not len(parts):
        return False
    total, reported = float(parts.sum()), float(dec.iloc[0])
    return abs(total - reported) <= abs(reported) * tol
