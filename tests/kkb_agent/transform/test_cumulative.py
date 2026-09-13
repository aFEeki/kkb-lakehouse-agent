"""SCRUM-93 - cumulative classification and de-cumulation.

The cases that matter are the ones where a naive classifier gets it wrong: a nominal
stock under high inflation looks monotone and cumulative-ish, and a smooth
non-resetting series is genuinely undecidable from pattern alone.
"""

from __future__ import annotations

import pandas as pd
import pytest

from kkb_agent.transform.cumulative import (
    CumulativeMode,
    classify,
    decumulate,
    resolve_by_statement,
    ytd_closure_holds,
)


def months(year_from: int, year_to: int) -> pd.DatetimeIndex:
    return pd.date_range(f"{year_from}-01-01", f"{year_to}-12-01", freq="MS")


def ytd_series(monthly: float, years: tuple[int, ...]) -> pd.Series:
    """Year-to-date accumulation: restarts each January."""
    data = {}
    for y in years:
        run = 0.0
        for m in range(1, 13):
            run += monthly
            data[pd.Timestamp(y, m, 1)] = run
    return pd.Series(data).sort_index()


def test_year_to_date_is_detected():
    ev = classify(ytd_series(100.0, (2021, 2022, 2023, 2024)))
    assert ev.mode is CumulativeMode.YTD
    assert ev.january_resets == 3
    assert ev.january_opportunities == 3


def test_stock_that_sometimes_falls_is_not_cumulative():
    idx = months(2021, 2024)
    vals = [1000 + i * 10 - (50 if i % 7 == 0 else 0) for i in range(len(idx))]
    ev = classify(pd.Series(vals, index=idx))
    assert ev.mode is CumulativeMode.NONE
    assert ev.negative_periods == 0


def test_monotone_nominal_stock_is_ambiguous_not_cumulative():
    """The central trap.

    A Turkish lira stock under 2021-2026 inflation rises every single month. It is NOT
    cumulative, but nothing in its shape says so — an inception-cumulative series looks
    exactly the same. The classifier must refuse to decide rather than guess.
    """
    idx = months(2021, 2025)
    vals = [1000 * (1.04**i) for i in range(len(idx))]  # ~60%/yr, never falls
    ev = classify(pd.Series(vals, index=idx))
    assert ev.mode is CumulativeMode.AMBIGUOUS
    assert ev.globally_monotone is True
    assert any("source definition" in n for n in ev.notes)


def test_short_series_is_ambiguous():
    idx = pd.date_range("2021-01-01", periods=6, freq="MS")
    ev = classify(pd.Series(range(6), index=idx))
    assert ev.mode is CumulativeMode.AMBIGUOUS


def test_decumulate_ytd_recovers_the_monthly_values():
    s = ytd_series(100.0, (2021, 2022))
    out = decumulate(s, CumulativeMode.YTD)
    assert out.round(6).eq(100.0).all()


def test_decumulate_none_is_a_passthrough():
    idx = months(2021, 2021)
    s = pd.Series(range(12), index=idx, dtype=float)
    pd.testing.assert_series_equal(decumulate(s, CumulativeMode.NONE), s)


def test_decumulate_refuses_when_ambiguous():
    s = ytd_series(100.0, (2021,))
    with pytest.raises(ValueError, match="resolve the ambiguity"):
        decumulate(s, CumulativeMode.AMBIGUOUS)


def test_ytd_closure_invariant_holds_and_fails_correctly():
    s = ytd_series(100.0, (2021, 2022))
    good = decumulate(s, CumulativeMode.YTD)
    assert ytd_closure_holds(s, good, 2021)

    broken = good.copy()
    broken.iloc[3] = broken.iloc[3] * 3  # corrupt one month
    assert not ytd_closure_holds(s, broken, 2021)


def test_missing_prior_observation_is_not_fabricated():
    s = ytd_series(100.0, (2021,))
    s.iloc[4] = float("nan")
    out = decumulate(s, CumulativeMode.YTD)
    assert out.isna().any()


class TestStatementKindResolution:
    """SCRUM-93 - settle ambiguous cases on accounting grounds, not by eye."""

    def test_income_statement_is_year_to_date(self):
        mode, why = resolve_by_statement(2, "Alınan Kar Payları", CumulativeMode.AMBIGUOUS)
        assert mode is CumulativeMode.YTD
        assert "income statement" in why

    def test_balance_sheet_does_not_accumulate(self):
        """A loan balance is what is outstanding, not every loan ever written."""
        mode, why = resolve_by_statement(4, "Bireysel Kredi Kartları", CumulativeMode.AMBIGUOUS)
        assert mode is CumulativeMode.NONE
        assert "balance sheet" in why

    def test_off_balance_sheet_commitments_are_positions(self):
        mode, _ = resolve_by_statement(14, "b) Kesin Teminat Mektupları", CumulativeMode.AMBIGUOUS)
        assert mode is CumulativeMode.NONE

    def test_period_profit_on_the_balance_sheet_is_still_year_to_date(self):
        """BDDK shows the P&L result on the balance sheet; it accumulates regardless."""
        mode, why = resolve_by_statement(1, "Dönem Karı (Zararı)", CumulativeMode.AMBIGUOUS)
        assert mode is CumulativeMode.YTD
        assert "balance sheet" in why

    def test_a_confident_pattern_result_is_never_overwritten(self):
        """Data disagreeing with the accounting is information, not noise."""
        mode, why = resolve_by_statement(4, "Tüketici Kredileri", CumulativeMode.YTD)
        assert mode is CumulativeMode.YTD
        assert "not consulted" in why

    def test_unknown_table_stays_ambiguous(self):
        mode, why = resolve_by_statement(99, "whatever", CumulativeMode.AMBIGUOUS)
        assert mode is CumulativeMode.AMBIGUOUS
        assert "no statement kind" in why
