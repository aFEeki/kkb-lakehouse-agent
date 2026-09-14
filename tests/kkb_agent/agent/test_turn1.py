"""SCRUM-44 - the first published question.

The parts that can be tested without a database are tested without one. The end-to-end
assembly needs the gold layer and is skipped when it is absent, so CI stays green on a
checkout with no data while the check still runs for anyone who has it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kkb_agent.agent.turn1 import BALANCE_KEY, CPI_KEY, RATE_KEY, build_turn1, find_decline_window

GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "lakehouse.duckdb"
needs_catalog = pytest.mark.skipif(not GOLD.exists(), reason="gold catalog not built")


class TestFindDeclineWindow:
    """The question asserts rates fell. Over 2021-2025 they roughly doubled, so the
    premise has to be tested rather than accepted - and rates *did* fall after the 2024
    peak, so rejecting it outright would be unhelpful."""

    def test_the_window_runs_from_the_peak_to_the_last_observation(self):
        assert find_decline_window((1.0, 5.0, 9.0, 7.0, 4.0)) == (2, 4)

    def test_a_series_that_never_declines_has_no_window(self):
        assert find_decline_window((1.0, 2.0, 3.0, 4.0)) is None

    def test_a_series_ending_at_its_peak_has_no_window(self):
        assert find_decline_window((1.0, 9.0, 2.0, 9.0)) is None

    def test_missing_values_do_not_anchor_the_window(self):
        """A gap is skipped, and the window ends at the last observation rather than at
        the last row."""
        assert find_decline_window((None, 2.0, 9.0, None, 4.0, None)) == (2, 4)

    def test_too_few_observations_to_call_a_trend(self):
        assert find_decline_window((5.0, 1.0)) is None
        assert find_decline_window((None, None, 3.0)) is None


@pytest.fixture(scope="module")
def result():
    """Built once: it opens the catalog and runs the whole turn."""
    return build_turn1(GOLD)


@needs_catalog
class TestTurnOneAgainstTheCatalog:
    def test_the_spine_is_sixty_months(self, result):
        assert len(result.frame.spine.values) == 60

    def test_all_three_series_plus_the_deflated_column_are_present(self, result):
        keys = [c.key for c in result.frame.columns]
        assert {BALANCE_KEY, RATE_KEY, CPI_KEY} <= set(keys)
        assert any("deflated_by" in k for k in keys)

    def test_the_rate_column_has_no_gaps(self, result):
        """It is weekly collapsed to monthly, so every month should be covered."""
        rate = next(c for c in result.frame.columns if c.key == RATE_KEY)
        assert rate.missing_count == 0

    def test_it_reports_the_premise_as_false_over_the_full_window(self, result):
        premise = next(f for f in result.frame.findings if f.finding_id == "f-premise")
        assert "yükseldi" in premise.statement

    def test_it_answers_the_question_over_the_window_where_rates_did_fall(self, result):
        decline = next(f for f in result.frame.findings if f.finding_id == "f-decline")
        assert "reel" in decline.statement.casefold()
        assert decline.supporting_column_keys

    def test_the_stock_versus_flow_distinction_is_always_stated(self, result):
        """Whether or not anyone asks. It is the failure a domain judge catches."""
        scope = next(f for f in result.frame.findings if f.finding_id == "f-olcum")
        assert "kullandırılan" in scope.statement

    def test_the_interest_rate_definition_is_named(self, result):
        caveats = " ".join(c for f in result.frame.findings for c in f.caveats)
        assert "TP.KTF12" in caveats
        assert "akım" in caveats

    def test_every_column_carries_its_provenance(self, result):
        for column in result.frame.columns:
            if column.origin != "source":
                continue
            source = column.lineage.sources[0]
            assert source.reference
            assert source.raw_sha256

    def test_it_completes_well_inside_a_demo_latency_budget(self, result):
        assert result.elapsed_seconds < 5.0

    def test_findings_reference_columns_that_exist(self, result):
        keys = {c.key for c in result.frame.columns}
        for finding in result.frame.findings:
            assert set(finding.supporting_column_keys) <= keys
