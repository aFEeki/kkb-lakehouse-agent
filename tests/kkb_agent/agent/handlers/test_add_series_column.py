"""SCRUM-45 - bringing a catalog series into the frame as a column.

This is the only operation that brings a number in from outside, so it is the only place
a wrong unit, a fabricated period or an unattributable figure can enter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.handlers import LoadedSeries, make_add_series_column_handler
from kkb_agent.agent.handlers.add_series_column import (
    SeriesColumnCollisionError,
    SeriesEmptyError,
    SeriesFrequencyError,
    SeriesMeasureError,
    SeriesNotFoundError,
    SeriesProvenanceError,
)
from kkb_agent.frame import AddColumnParameters, AnalysisFrame, Operation, OperationType, Spine

MONTHS = tuple(date(2021, m, 1) for m in range(1, 7))


class FakeSource:
    def __init__(self, *series: LoadedSeries):
        self._by_id = {s.series_id: s for s in series}

    def fetch(self, series_reference: str) -> LoadedSeries | None:
        return self._by_id.get(series_reference)


def series(series_id: str = "s1", **kw) -> LoadedSeries:
    base = dict(
        series_id=series_id,
        name="Tüketici Kredileri - Konut",
        values=dict.fromkeys(MONTHS, 100.0),
        native_freq="M",
        aggregation_rule="last",
        measure_type="stock",
        unit_raw="milyon TL",
        unit_normalized="TRY",
        scale_factor=1_000_000.0,
        source="bddk_aylik",
        source_hash="a" * 64,
        retrieved_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    base.update(kw)
    return LoadedSeries(**base)


def frame(columns=()) -> AnalysisFrame:
    return AnalysisFrame(frame_id="f", spine=Spine(values=MONTHS), columns=tuple(columns))


def add(key: str = "konut", ref: str = "s1", version: int = 0) -> Operation:
    return Operation(
        operation_id=f"op{version}",
        kind=OperationType.ADD_COLUMN,
        parameters=AddColumnParameters(series_reference=ref, column_key=key),
        timestamp=datetime.now(UTC),
        source_version=version,
        resulting_version=version + 1,
    )


def run(source, f=None, op=None):
    return make_add_series_column_handler(source)(f or frame(), op or add())


class TestTheColumnItBuilds:
    def test_values_land_on_the_spine_in_order(self):
        s = series(values=dict(zip(MONTHS, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], strict=True)))
        column = run(FakeSource(s)).columns[0]
        assert column.values == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    def test_the_unit_and_its_scale_travel_with_the_column(self):
        """Without the scale a milyon TL column and a bin TL column are a 1000x apart and
        look identical."""
        column = run(FakeSource(series())).columns[0]
        assert column.unit.symbol == "TRY"
        assert column.unit.scale == 1_000_000.0

    def test_lineage_records_where_the_number_came_from(self):
        column = run(FakeSource(series())).columns[0]
        source_ref = column.lineage.sources[0]
        assert source_ref.source_type == "bddk_aylik"
        assert source_ref.reference == "s1"
        assert source_ref.raw_sha256 == "a" * 64
        assert source_ref.retrieved_at == datetime(2026, 9, 13, tzinfo=UTC)

    def test_the_facets_are_recorded_so_two_cuts_are_distinguishable(self):
        """A frame holding Katılım's housing loans and one holding the sector's are
        otherwise identical once the numbers are on a chart."""
        s = series(metadata=(("sector_scope", "Katılım"),))
        column = run(FakeSource(s)).columns[0]
        assert ("sector_scope", "Katılım") in [
            (m.key, m.value) for m in column.lineage.sources[0].metadata
        ]

    def test_a_count_is_a_number_column_because_the_catalog_stores_doubles(self):
        """Column forbids implicit coercion, so claiming "integer" for a value stored as
        10569.0 would be a claim the data does not support. measure_type still says it
        counts."""
        s = series(measure_type="count", unit_normalized="adet", scale_factor=1.0)
        column = run(FakeSource(s)).columns[0]
        assert column.dtype == "number"
        assert column.measure_type == "count"


class TestTheSpineIsFixed:
    def test_a_series_is_collapsed_onto_the_spine_frequency(self):
        """A weekly rate added to a monthly frame is averaged into months, and the
        resampling is recorded as a transformation rather than left implicit."""
        weekly = {date(2021, 1, d): float(d) for d in (1, 8, 15, 22)}
        s = series(values=weekly, native_freq="W", aggregation_rule="mean", measure_type="rate")
        column = run(FakeSource(s)).columns[0]
        assert column.values[0] == pytest.approx(11.5)
        assert [t.name for t in column.lineage.transformations] == ["collapse_frequency"]

    def test_a_coarser_series_is_refused_rather_than_expanded(self):
        """Quarterly onto a monthly spine would invent two observations per quarter."""
        s = series(values={date(2021, 1, 1): 1.0, date(2021, 4, 1): 2.0}, native_freq="Q")
        with pytest.raises(SeriesFrequencyError):
            run(FakeSource(s))

    def test_a_period_the_series_does_not_reach_is_none_not_filled(self):
        """The ragged edge shows through as missing data, never as a carried-forward or
        zero value."""
        s = series(values={MONTHS[0]: 1.0, MONTHS[1]: 2.0})
        column = run(FakeSource(s)).columns[0]
        assert column.values == (1.0, 2.0, None, None, None, None)
        assert column.missing_count == 4

    def test_existing_columns_are_untouched(self):
        first = run(FakeSource(series()))
        second = make_add_series_column_handler(FakeSource(series("s2", name="İkinci")))(
            first, add(key="ikinci", ref="s2", version=1)
        )
        assert second.columns[0] == first.columns[0]
        assert [c.key for c in second.columns] == ["konut", "ikinci"]
        assert second.spine == first.spine


class TestWhatItRefuses:
    def test_an_unknown_series_is_an_error_not_an_empty_column(self):
        with pytest.raises(SeriesNotFoundError):
            run(FakeSource(series()), op=add(ref="missing"))

    def test_a_duplicate_column_key_is_refused(self):
        first = run(FakeSource(series()))
        with pytest.raises(SeriesColumnCollisionError):
            make_add_series_column_handler(FakeSource(series()))(first, add(version=1))

    def test_a_series_with_no_provenance_is_refused(self):
        """A frame full of unattributable numbers is worse than an error."""
        with pytest.raises(SeriesProvenanceError):
            run(FakeSource(series(source="")))

    def test_a_series_with_no_observation_on_this_spine_is_refused(self):
        s = series(values={date(2019, 1, 1): 5.0})
        with pytest.raises(SeriesEmptyError):
            run(FakeSource(s))

    def test_an_undetermined_measure_type_is_refused(self):
        with pytest.raises(SeriesMeasureError):
            run(FakeSource(series(measure_type="unknown")))


class TestRegistration:
    def test_add_column_is_unavailable_without_a_source(self):
        """The executor's own "valid but has no handler" is a better error than a handler
        failing later against an empty catalog."""
        assert OperationType.ADD_COLUMN not in create_operation_executor().supported_operations

    def test_add_column_is_available_once_a_source_is_supplied(self):
        executor = create_operation_executor(series_source=FakeSource(series()))
        assert OperationType.ADD_COLUMN in executor.supported_operations

    def test_the_executor_advances_the_version_and_records_the_operation(self):
        executor = create_operation_executor(series_source=FakeSource(series()))
        after = executor.execute(frame(), add())
        assert after.version == 1
        assert after.operations[-1].kind is OperationType.ADD_COLUMN
        assert len(after.columns) == 1

    def test_a_domain_error_reaches_the_caller_unwrapped(self):
        """The executor re-raises OperationExecutionError subclasses untouched, so the
        caller sees "no such series" rather than a generic handler failure."""
        executor = create_operation_executor(series_source=FakeSource(series()))
        with pytest.raises(SeriesNotFoundError):
            executor.execute(frame(), add(ref="missing"))
