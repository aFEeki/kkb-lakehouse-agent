"""SCRUM-44 - the first published question.

The parts that can be tested without a database are tested without one. The end-to-end
assembly needs the gold layer and is skipped when it is absent, so CI stays green on a
checkout with no data while the check still runs for anyone who has it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kkb_agent.agent.turn1 import (
    BALANCE_KEY,
    RATE_KEY,
    TurnOneSpineError,
    _rate_caveats,
    _scripted_plan,
    _validate_published_spine,
    build_turn1,
    find_decline_window,
)
from kkb_agent.api.main import _turn1_catalog_ready
from kkb_agent.frame import AnalysisFrame, OperationType, Spine

GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "lakehouse.duckdb"
needs_catalog = pytest.mark.skipif(
    not _turn1_catalog_ready(GOLD), reason="populated gold catalog not built"
)


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


def test_turn_one_script_adds_only_nominal_balance_and_rate():
    class RecordingExecutor:
        def __init__(self):
            self.operations = []

        def execute(self, frame, operation):
            self.operations.append(operation)
            return frame.model_copy(
                update={
                    "version": operation.resulting_version,
                    "operations": (*frame.operations, operation),
                }
            )

    executor = RecordingExecutor()
    frame = AnalysisFrame(frame_id="turn-one", spine=Spine(values=[]))
    available = {BALANCE_KEY: "balance-series", RATE_KEY: "rate-series"}

    result = _scripted_plan(executor, frame, available)

    assert result.version == 2
    assert [operation.kind for operation in executor.operations] == [
        OperationType.ADD_COLUMN,
        OperationType.ADD_COLUMN,
    ]
    assert [operation.parameters.column_key for operation in executor.operations] == [
        BALANCE_KEY,
        RATE_KEY,
    ]
    assert all(
        "cpi" not in operation.parameters.series_reference for operation in executor.operations
    )


def test_rate_caveat_uses_the_resolved_series_identifier():
    caveats = _rate_caveats("evds.actual-resolved-rate")
    assert "evds.actual-resolved-rate" in caveats[0]
    assert "TP.KTF12" not in caveats[0]


def test_published_spine_requires_all_sixty_contiguous_months():
    periods = tuple(date(2021 + index // 12, index % 12 + 1, 1) for index in range(60))
    _validate_published_spine(periods, date(2021, 1, 1), date(2025, 12, 1))
    for invalid in (periods[:-1], periods[:20] + periods[21:], periods[:10] + periods[9:]):
        with pytest.raises(TurnOneSpineError, match="60 contiguous"):
            _validate_published_spine(invalid, date(2021, 1, 1), date(2025, 12, 1))


@pytest.fixture(scope="module")
def result():
    """Built once: it opens the catalog and runs the whole turn."""
    return build_turn1(GOLD)


@needs_catalog
class TestTurnOneAgainstTheCatalog:
    def test_the_spine_is_sixty_months(self, result):
        assert len(result.frame.spine.values) == 60

    def test_only_the_nominal_balance_and_rate_are_present(self, result):
        keys = [c.key for c in result.frame.columns]
        assert keys == [BALANCE_KEY, RATE_KEY]
        assert all("deflated_by" not in key for key in keys)

    def test_the_rate_column_has_no_gaps(self, result):
        """It is weekly collapsed to monthly, so every month should be covered."""
        rate = next(c for c in result.frame.columns if c.key == RATE_KEY)
        assert rate.missing_count == 0

    def test_it_reports_the_premise_as_false_over_the_full_window(self, result):
        premise = next(f for f in result.frame.findings if f.finding_id == "f-premise")
        assert "yükseldi" in premise.statement

    def test_it_answers_the_question_over_the_window_where_rates_did_fall(self, result):
        decline = next(f for f in result.frame.findings if f.finding_id == "f-decline")
        assert "nominal" in decline.statement.casefold()
        assert "turn 2" in decline.statement.casefold()
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
