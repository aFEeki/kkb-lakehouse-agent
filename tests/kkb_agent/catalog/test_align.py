"""SCRUM-26 / SCRUM-30 - one time axis from series of different frequencies and ends."""

from __future__ import annotations

from datetime import date

import pytest

from kkb_agent.catalog.align import (
    SeriesSpec,
    UpsampleRefused,
    align,
    can_collapse,
    collapse,
)
from kkb_agent.catalog.schema import AggregationRule, Frequency


def months(year: int, first: int, last: int) -> list[date]:
    return [date(year, m, 1) for m in range(first, last + 1)]


def spec(series_id: str, values: dict, freq: str, rule: str, **kw) -> SeriesSpec:
    return SeriesSpec(
        series_id=series_id,
        name=kw.pop("name", series_id),
        values=values,
        native_freq=freq,
        aggregation_rule=rule,
        **kw,
    )


class TestCollapseDirection:
    def test_a_finer_series_may_be_collapsed(self):
        assert can_collapse(Frequency.WEEKLY, Frequency.MONTHLY)
        assert can_collapse(Frequency.MONTHLY, Frequency.QUARTERLY)

    def test_a_coarser_series_may_not_be_expanded(self):
        """Quarterly to monthly invents two observations that were never published."""
        assert not can_collapse(Frequency.QUARTERLY, Frequency.MONTHLY)

    def test_expansion_raises_rather_than_dropping_the_column(self):
        """A table quietly missing the column that answers the question is worse than an
        error the caller has to handle."""
        quarterly = spec(
            "finturk.x",
            {date(2021, 3, 1): 100.0, date(2021, 6, 1): 120.0},
            Frequency.QUARTERLY,
            AggregationRule.LAST,
        )
        with pytest.raises(UpsampleRefused, match="never published"):
            collapse(quarterly, Frequency.MONTHLY)


class TestAggregationFollowsTheMeasure:
    """The rule comes off the series, because it is a property of what the number is."""

    def test_a_flow_sums_over_the_period(self):
        flow = spec(
            "f",
            {d: 10.0 for d in months(2021, 1, 6)},
            Frequency.MONTHLY,
            AggregationRule.SUM,
        )
        out = collapse(flow, Frequency.QUARTERLY)
        assert list(out.values()) == [30.0, 30.0]

    def test_a_balance_takes_the_period_end(self):
        stock = spec(
            "s",
            dict(zip(months(2021, 1, 6), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], strict=True)),
            Frequency.MONTHLY,
            AggregationRule.LAST,
        )
        assert list(collapse(stock, Frequency.QUARTERLY).values()) == [3.0, 6.0]

    def test_a_rate_averages(self):
        """Summing twelve monthly interest rates gives a number around 500%."""
        rate = spec(
            "r",
            dict(zip(months(2021, 1, 6), [10.0, 20.0, 30.0, 40.0, 50.0, 60.0], strict=True)),
            Frequency.MONTHLY,
            AggregationRule.MEAN,
        )
        assert list(collapse(rate, Frequency.QUARTERLY).values()) == [20.0, 50.0]


class TestNoSilentFill:
    """Invariant I7 - a period with no observation is None, never a number."""

    def test_an_empty_bucket_is_null_not_zero(self):
        """`sum` returns 0.0 for an empty bucket, and nothing downstream can tell that
        from an observed zero."""
        sparse = spec(
            "f",
            {date(2021, 1, 1): 10.0, date(2021, 2, 1): 10.0, date(2021, 7, 1): 10.0},
            Frequency.MONTHLY,
            AggregationRule.SUM,
        )
        out = collapse(sparse, Frequency.QUARTERLY)
        quarters = sorted(out)
        assert out[quarters[0]] == 20.0  # Q1: two months observed
        assert out[quarters[1]] is None  # Q2: nothing observed - not 0.0

    def test_a_gap_is_not_carried_forward(self):
        stock = spec(
            "s",
            {date(2021, 1, 1): 5.0, date(2021, 3, 1): 7.0},
            Frequency.MONTHLY,
            AggregationRule.LAST,
        )
        out = collapse(stock, Frequency.MONTHLY)
        assert date(2021, 2, 1) not in out or out.get(date(2021, 2, 1)) is None


class TestRaggedEdge:
    """SCRUM-30 / DECISIONS #11 - truncate to the common end, and say so."""

    @pytest.fixture
    def mixed(self) -> list[SeriesSpec]:
        return [
            spec(
                "long",
                dict.fromkeys(months(2021, 1, 6), 1.0),
                Frequency.MONTHLY,
                AggregationRule.LAST,
                name="BDDK aylık",
            ),
            spec(
                "short",
                dict.fromkeys(months(2021, 1, 4), 2.0),
                Frequency.MONTHLY,
                AggregationRule.LAST,
                name="EVDS",
            ),
        ]

    def test_the_axis_stops_where_the_shortest_series_stops(self, mixed):
        a = align(mixed)
        assert a.spine[-1] == date(2021, 4, 1)
        assert a.truncated_to == date(2021, 4, 1)

    def test_it_names_who_forced_the_truncation(self, mixed):
        assert align(mixed).truncated_by == ("short",)

    def test_the_notice_is_written_only_when_something_was_given_up(self, mixed):
        """An unconditional notice trains people to ignore it."""
        assert "2021-04-01" in align(mixed).notice_tr()

        equal = [
            mixed[0],
            spec(
                "other",
                dict.fromkeys(months(2021, 1, 6), 3.0),
                Frequency.MONTHLY,
                AggregationRule.LAST,
            ),
        ]
        assert align(equal).notice_tr() == ""
        assert align(equal).truncated_to is None

    def test_truncation_can_be_declined_for_a_single_series_view(self, mixed):
        a = align(mixed, truncate_to_common_end=False)
        assert a.spine[-1] == date(2021, 6, 1)
        assert a.truncated_to is None


class TestAlign:
    def test_the_default_axis_is_the_coarsest_present(self):
        """The only frequency nothing has to be expanded into."""
        a = align(
            [
                spec(
                    "w",
                    dict.fromkeys(months(2021, 1, 6), 1.0),
                    Frequency.MONTHLY,
                    AggregationRule.LAST,
                ),
                spec(
                    "q",
                    {date(2021, 3, 1): 5.0, date(2021, 6, 1): 6.0},
                    Frequency.QUARTERLY,
                    AggregationRule.LAST,
                ),
            ]
        )
        assert a.frequency == Frequency.QUARTERLY

    def test_asking_for_finer_than_a_member_series_raises(self):
        with pytest.raises(UpsampleRefused):
            align(
                [spec("q", {date(2021, 3, 1): 5.0}, Frequency.QUARTERLY, AggregationRule.LAST)],
                frequency=Frequency.MONTHLY,
            )

    def test_every_series_spans_the_whole_spine(self):
        """Columns must be the same length or the table cannot be rendered."""
        a = align(
            [
                spec(
                    "a",
                    dict.fromkeys(months(2021, 1, 6), 1.0),
                    Frequency.MONTHLY,
                    AggregationRule.LAST,
                ),
                spec(
                    "b",
                    dict.fromkeys(months(2021, 3, 6), 2.0),
                    Frequency.MONTHLY,
                    AggregationRule.LAST,
                ),
            ]
        )
        for s in a.series:
            assert set(s.values) == set(a.spine)

    def test_a_series_with_no_observations_is_dropped_and_recorded(self):
        a = align(
            [
                spec(
                    "real",
                    dict.fromkeys(months(2021, 1, 3), 1.0),
                    Frequency.MONTHLY,
                    AggregationRule.LAST,
                ),
                spec("empty", {}, Frequency.MONTHLY, AggregationRule.LAST),
            ]
        )
        assert a.dropped == ("empty",)
        assert [s.series_id for s in a.series] == ["real"]

    def test_resampling_is_recorded_on_the_series_it_happened_to(self):
        a = align(
            [
                spec(
                    "m",
                    dict.fromkeys(months(2021, 1, 6), 1.0),
                    Frequency.MONTHLY,
                    AggregationRule.LAST,
                ),
                spec(
                    "q",
                    {date(2021, 3, 1): 5.0, date(2021, 6, 1): 6.0},
                    Frequency.QUARTERLY,
                    AggregationRule.LAST,
                ),
            ]
        )
        by_id = {s.series_id: s for s in a.series}
        assert by_id["m"].resampled_from == Frequency.MONTHLY
        assert by_id["q"].resampled_from is None

    def test_nothing_in_produces_an_empty_alignment_not_an_error(self):
        assert align([]).spine == ()

    def test_a_resampled_series_lands_on_the_same_rows_as_an_unresampled_one(self):
        """The catalog labels a period by its start. A resample anchored to period end
        labels January 2021-01-31, so the column that needed resampling and the column
        that did not land on different rows and the table cannot be assembled.
        """
        monthly = spec(
            "already-q",
            {date(2021, 1, 1): 1.0, date(2021, 4, 1): 2.0},
            Frequency.QUARTERLY,
            AggregationRule.LAST,
        )
        needs_resample = spec(
            "m-to-q",
            dict.fromkeys(months(2021, 1, 6), 5.0),
            Frequency.MONTHLY,
            AggregationRule.LAST,
        )
        a = align([monthly, needs_resample], frequency=Frequency.QUARTERLY)

        assert a.spine == (date(2021, 1, 1), date(2021, 4, 1))
        for s in a.series:
            assert all(v is not None for v in s.values.values())
