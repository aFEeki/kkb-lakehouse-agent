"""Deterministically rebase one numeric column to 100 at an exact date."""

import math
from datetime import date

from kkb_agent.agent.executor import OperationExecutionError
from kkb_agent.frame import (
    AnalysisFrame,
    Column,
    IndexColumnParameters,
    Lineage,
    MetadataEntry,
    Operation,
    OperationType,
    ParentLineage,
    Transformation,
    Unit,
)

TRANSFORMATION_VERSION = "1"


class IndexColumnError(OperationExecutionError):
    """Base error for a rejected index-column operation."""


class IndexColumnNotFoundError(IndexColumnError):
    """The requested source column is absent."""


class IndexColumnTypeError(IndexColumnError):
    """The source column is not numeric."""


class IndexSpineTypeError(IndexColumnError):
    """The operation cannot exactly identify a row on this spine kind."""


class IndexBasePeriodNotFoundError(IndexColumnError):
    """The exact requested base date is absent from the spine."""


class IndexBasePeriodMissingError(IndexColumnError):
    """The source value at the exact base date is missing."""


class IndexBaseValueZeroError(IndexColumnError):
    """The base value is zero and cannot be used as a divisor."""


class IndexColumnCollisionError(IndexColumnError):
    """The deterministic derived-column key is already present."""


class IndexCalculationError(IndexColumnError):
    """Index calculation produced a non-finite or otherwise invalid value."""


def indexed_column_key(column_key: str, base_date: date) -> str:
    """Return the stable key derived only from source key and base date."""
    return f"{column_key}__index_{base_date.isoformat()}"


def index_column_handler(frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
    """Return an uncommitted candidate with one indexed derived column appended."""
    if operation.kind is not OperationType.INDEX_COLUMN or not isinstance(
        operation.parameters, IndexColumnParameters
    ):
        raise IndexColumnError("index_column handler requires typed IndexColumnParameters")

    parameters = operation.parameters
    source = next((column for column in frame.columns if column.key == parameters.column_key), None)
    if source is None:
        raise IndexColumnNotFoundError(
            f"Column {parameters.column_key!r} does not exist in frame {frame.frame_id!r}"
        )
    if source.dtype not in {"integer", "number"}:
        raise IndexColumnTypeError(f"Column {source.key!r} has non-numeric dtype {source.dtype!r}")
    if frame.spine.kind != "date":
        raise IndexSpineTypeError(
            "index_column base_date requires a date spine; datetime rows need an exact "
            "timestamp contract"
        )

    try:
        base_position = frame.spine.values.index(parameters.base_date)
    except ValueError as exc:
        raise IndexBasePeriodNotFoundError(
            f"Base date {parameters.base_date.isoformat()} does not exist on the frame spine"
        ) from exc

    base_value = source.values[base_position]
    if base_value is None:
        raise IndexBasePeriodMissingError(
            f"Column {source.key!r} is missing at exact base date "
            f"{parameters.base_date.isoformat()}"
        )
    if type(base_value) not in {int, float} or not math.isfinite(base_value):
        raise IndexCalculationError(f"Column {source.key!r} has an invalid base value")
    if base_value == 0:
        raise IndexBaseValueZeroError(
            f"Column {source.key!r} has zero at base date {parameters.base_date.isoformat()}"
        )

    output_key = indexed_column_key(source.key, parameters.base_date)
    if any(column.key == output_key for column in frame.columns):
        raise IndexColumnCollisionError(f"Derived column {output_key!r} already exists")

    values: list[float | None] = []
    for value in source.values:
        if value is None:
            values.append(None)
            continue
        if type(value) not in {int, float} or not math.isfinite(value):
            raise IndexCalculationError(f"Column {source.key!r} contains an invalid numeric value")
        try:
            indexed_value = value / base_value * 100.0
        except (ArithmeticError, OverflowError) as exc:
            raise IndexCalculationError(
                f"Index calculation failed for column {source.key!r}"
            ) from exc
        if not math.isfinite(indexed_value):
            raise IndexCalculationError(
                f"Index calculation produced a non-finite value for column {source.key!r}"
            )
        values.append(indexed_value)

    base_label = parameters.base_date.isoformat()
    indexed = Column(
        key=output_key,
        label=f"{source.label} (Index, {base_label} = 100)",
        dtype="number",
        values=values,
        measure_type="index",
        unit=Unit(symbol="index", description=f"Dimensionless index; {base_label} = 100"),
        origin="derived",
        lineage=Lineage(
            parents=[
                ParentLineage(
                    frame_id=frame.frame_id,
                    frame_version=frame.version,
                    column_key=source.key,
                    lineage=source.lineage,
                )
            ],
            transformations=[
                Transformation(
                    name=OperationType.INDEX_COLUMN.value,
                    implementation_version=TRANSFORMATION_VERSION,
                    parameters=[
                        MetadataEntry(key="base_date", value=base_label),
                        MetadataEntry(key="base_value", value=100),
                    ],
                )
            ],
        ),
    )
    return AnalysisFrame.model_validate(
        {**frame.model_dump(), "columns": (*frame.columns, indexed)}
    )
