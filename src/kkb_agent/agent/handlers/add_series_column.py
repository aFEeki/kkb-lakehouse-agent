"""SCRUM-45 - put a catalog series into the frame as a column.

This is the seam between the data layer and the analysis object, and it is the only
operation that brings a number in from outside. Everything else - deflate, index, revert -
rearranges what is already here.

**The spine is fixed, so the series bends.** `assert_spine_intact` forbids an operation
from changing row identity, which is the right rule: adding a column must never silently
re-cut the table someone is already reading. So a series is collapsed to the spine's
frequency and projected onto the spine's exact periods, with None where it has nothing.
A weekly rate added to a monthly frame is averaged into months; a quarterly series added
to a monthly frame is refused, because expanding it would invent observations.

**None is a real answer here.** A series that starts later than the frame, or ends
earlier, contributes Nones at the ends. That is the ragged edge showing through as missing
data rather than as a fabricated value, and `Column.missing_count` makes it countable.

**Lineage is not decoration.** The catalog carries source_hash and retrieved_at for all
47,015 series precisely so this can record what a number came from. A column whose origin
cannot be stated is refused rather than added with an empty lineage, because `Lineage`
itself requires a source and a frame full of unattributable numbers is worse than an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from kkb_agent.agent.executor import OperationExecutionError
from kkb_agent.catalog.align import SeriesSpec, UpsampleRefused, collapse, infer_spine_frequency
from kkb_agent.frame import (
    AddColumnParameters,
    AnalysisFrame,
    Column,
    Lineage,
    MetadataEntry,
    Operation,
    OperationType,
    SourceReference,
    Transformation,
    Unit,
)

TRANSFORMATION_VERSION = "1"

# Spine kinds this operation can place a series on. A datetime spine is not supported
# because every catalog series is dated to a period, not an instant, and matching a date
# against a timestamp would need a timezone convention nothing has agreed on.
_SUPPORTED_SPINE_KIND = "date"

# measure_type -> the frame's column dtype. The mapping exists so an unexpected measure
# type is refused rather than defaulted.
#
# A count maps to "number", not "integer". The catalog stores every value as a double, and
# Column forbids implicit coercion - rightly, since silently narrowing 10569.0 to an int
# is the sort of quiet conversion that loses a fractional value somewhere it mattered.
# Counting is what measure_type says; dtype only describes how the value is stored.
_DTYPE = {
    "stock": "number",
    "flow": "number",
    "rate": "number",
    "ratio": "number",
    "index": "number",
    "count": "number",
}


class AddSeriesColumnError(OperationExecutionError):
    """Base error for a rejected add-series-column operation."""


class SeriesNotFoundError(AddSeriesColumnError):
    """The catalog has no series with the requested reference."""


class SeriesColumnCollisionError(AddSeriesColumnError):
    """The requested column key is already present."""


class SeriesSpineTypeError(AddSeriesColumnError):
    """The frame's spine is not one this operation can place a series on."""


class SeriesFrequencyError(AddSeriesColumnError):
    """The series is coarser than the spine and cannot be expanded onto it."""


class SeriesProvenanceError(AddSeriesColumnError):
    """The series cannot state where it came from."""


class SeriesEmptyError(AddSeriesColumnError):
    """The series has no observation anywhere on this spine."""


class SeriesMeasureError(AddSeriesColumnError):
    """The series' measure type has no column dtype."""


@dataclass(frozen=True)
class LoadedSeries:
    """One catalog series with everything a column needs, and nothing more.

    A plain record rather than the catalog's own type, so the frame layer never imports
    DuckDB and the handler can be tested without a database.
    """

    series_id: str
    name: str
    values: dict[date, float | None]
    native_freq: str
    aggregation_rule: str
    measure_type: str
    unit_raw: str = ""
    unit_normalized: str = ""
    scale_factor: float = 1.0
    source: str = ""
    source_hash: str = ""
    retrieved_at: datetime | None = None
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


class SeriesSource(Protocol):
    """Where a series_reference is resolved to data. Implemented over the catalog."""

    def fetch(self, series_reference: str) -> LoadedSeries | None: ...


def _unit_for(series: LoadedSeries) -> Unit | None:
    if not series.unit_normalized:
        return None
    return Unit(
        symbol=series.unit_normalized,
        scale=series.scale_factor if series.scale_factor > 0 else 1.0,
        description=series.unit_raw or None,
    )


def _lineage_for(series: LoadedSeries, resampled_from: str | None) -> Lineage:
    if not series.source or not series.series_id:
        raise SeriesProvenanceError(
            f"{series.series_id!r} cannot state its source; refusing to add an "
            "unattributable column"
        )
    transformations = []
    if resampled_from:
        transformations.append(
            Transformation(
                name="collapse_frequency",
                implementation_version=TRANSFORMATION_VERSION,
                parameters=(
                    MetadataEntry(key="from", value=resampled_from),
                    MetadataEntry(key="rule", value=series.aggregation_rule),
                ),
            )
        )
    return Lineage(
        sources=(
            SourceReference(
                source_type=series.source,
                reference=series.series_id,
                retrieved_at=series.retrieved_at,
                raw_sha256=series.source_hash or None,
                metadata=tuple(MetadataEntry(key=k, value=v) for k, v in series.metadata),
            ),
        ),
        transformations=tuple(transformations),
    )


def make_add_series_column_handler(source: SeriesSource):
    """Build the handler around a source of series. The executor calls the result."""

    def add_series_column_handler(frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
        if operation.kind is not OperationType.ADD_COLUMN or not isinstance(
            operation.parameters, AddColumnParameters
        ):
            raise AddSeriesColumnError(
                "add_series_column handler requires typed AddColumnParameters"
            )

        params = operation.parameters
        if any(column.key == params.column_key for column in frame.columns):
            raise SeriesColumnCollisionError(
                f"Column {params.column_key!r} is already present in the frame"
            )

        if frame.spine.kind != _SUPPORTED_SPINE_KIND:
            raise SeriesSpineTypeError(
                f"Spine kind {frame.spine.kind!r} is not supported; catalog series are "
                "dated to a period, not an instant"
            )

        series = source.fetch(params.series_reference)
        if series is None:
            raise SeriesNotFoundError(f"No series {params.series_reference!r} in the catalog")

        dtype = _DTYPE.get(series.measure_type)
        if dtype is None:
            raise SeriesMeasureError(
                f"{series.series_id!r} has measure type {series.measure_type!r}, which has "
                "no column dtype; it is not servable"
            )

        spine_values: tuple[date, ...] = tuple(frame.spine.values)
        target = infer_spine_frequency(spine_values)
        spec = SeriesSpec(
            series_id=series.series_id,
            name=series.name,
            values=series.values,
            native_freq=series.native_freq,
            aggregation_rule=series.aggregation_rule,
            unit=series.unit_raw,
            scale_factor=series.scale_factor,
            measure_type=series.measure_type,
        )

        try:
            collapsed = collapse(spec, target)
        except UpsampleRefused as exc:
            raise SeriesFrequencyError(str(exc)) from exc

        # Projected onto the spine's own periods. A period the series does not reach is
        # None, never carried forward and never zero.
        values = tuple(collapsed.get(period) for period in spine_values)
        if all(value is None for value in values):
            raise SeriesEmptyError(
                f"{series.series_id!r} has no observation on this spine "
                f"({spine_values[0]} to {spine_values[-1]})"
                if spine_values
                else f"{series.series_id!r} has no observation and the spine is empty"
            )

        column = Column(
            key=params.column_key,
            label=series.name or series.series_id,
            dtype=dtype,
            values=values,
            measure_type=series.measure_type,
            unit=_unit_for(series),
            origin="source",
            lineage=_lineage_for(
                series, series.native_freq if target != series.native_freq else None
            ),
        )

        return frame.model_copy(update={"columns": (*frame.columns, column)})

    return add_series_column_handler
