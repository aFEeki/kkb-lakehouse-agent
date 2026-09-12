"""Deflate one nominal stock using the settled headline-CPI convention."""

import math
from datetime import date

from kkb_agent.agent.executor import OperationExecutionError
from kkb_agent.frame import (
    AnalysisFrame,
    Column,
    DeflateColumnParameters,
    Lineage,
    MetadataEntry,
    Operation,
    OperationType,
    ParentLineage,
    Transformation,
)

DEFLATION_CONVENTION = "cpi_base_period_constant_prices_v1"
TRANSFORMATION_VERSION = "1"


class DeflateColumnError(OperationExecutionError):
    """Base error for a rejected deflate-column operation."""


class DeflateConventionError(DeflateColumnError):
    """The requested convention is not the settled production convention."""


class DeflateColumnNotFoundError(DeflateColumnError):
    """The requested target or deflator column is absent."""


class DeflateColumnTypeError(DeflateColumnError):
    """The target or deflator column is not numeric."""


class DeflateTargetMeasureError(DeflateColumnError):
    """The target is not classified as a monetary stock."""


class DeflateDeflatorMeasureError(DeflateColumnError):
    """The deflator is not classified as a price-index level."""


class DeflateSpineTypeError(DeflateColumnError):
    """The base date cannot exactly identify a row on this spine kind."""


class DeflateBasePeriodNotFoundError(DeflateColumnError):
    """The exact requested base date is absent from the spine."""


class DeflateBasePeriodMissingError(DeflateColumnError):
    """The CPI level at the exact base date is missing."""


class DeflateDeflatorValueError(DeflateColumnError):
    """A CPI level used by the calculation is non-positive or invalid."""


class DeflateColumnCollisionError(DeflateColumnError):
    """The deterministic derived-column key is already present."""


class DeflateCalculationError(DeflateColumnError):
    """Deflation produced a non-finite or otherwise invalid value."""


def deflated_column_key(column_key: str, deflator_column_key: str, base_date: date) -> str:
    """Return the stable key derived from target, deflator and base date."""
    return f"{column_key}__deflated_by_{deflator_column_key}_{base_date.isoformat()}"


def _numeric_column(column: Column, role: str) -> None:
    if column.dtype not in {"integer", "number"}:
        raise DeflateColumnTypeError(
            f"{role} column {column.key!r} has non-numeric dtype {column.dtype!r}"
        )


def _valid_cpi(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def deflate_column_handler(frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
    """Return an uncommitted candidate with one constant-price column appended."""
    if operation.kind is not OperationType.DEFLATE_COLUMN or not isinstance(
        operation.parameters, DeflateColumnParameters
    ):
        raise DeflateColumnError("deflate_column handler requires typed DeflateColumnParameters")

    parameters = operation.parameters
    if parameters.convention_reference != DEFLATION_CONVENTION:
        raise DeflateConventionError(
            f"Unsupported deflation convention {parameters.convention_reference!r}; "
            f"expected {DEFLATION_CONVENTION!r}"
        )

    columns = {column.key: column for column in frame.columns}
    target = columns.get(parameters.column_key)
    deflator = columns.get(parameters.deflator_column_key)
    if target is None:
        raise DeflateColumnNotFoundError(f"Target column {parameters.column_key!r} does not exist")
    if deflator is None:
        raise DeflateColumnNotFoundError(
            f"Deflator column {parameters.deflator_column_key!r} does not exist"
        )
    _numeric_column(target, "Target")
    _numeric_column(deflator, "Deflator")
    if target.measure_type != "stock":
        raise DeflateTargetMeasureError(
            f"Target column {target.key!r} must have measure_type 'stock', not "
            f"{target.measure_type!r}"
        )
    if deflator.measure_type != "index":
        raise DeflateDeflatorMeasureError(
            f"Deflator column {deflator.key!r} must be a CPI price-level index, not "
            f"measure_type {deflator.measure_type!r}"
        )
    if frame.spine.kind != "date":
        raise DeflateSpineTypeError(
            "deflate_column base_date requires a date spine; datetime rows need an exact "
            "timestamp contract"
        )

    try:
        base_position = frame.spine.values.index(parameters.base_date)
    except ValueError as exc:
        raise DeflateBasePeriodNotFoundError(
            f"Base date {parameters.base_date.isoformat()} does not exist on the frame spine"
        ) from exc

    cpi_base = deflator.values[base_position]
    if cpi_base is None:
        raise DeflateBasePeriodMissingError(
            f"CPI is missing at exact base date {parameters.base_date.isoformat()}"
        )
    if not _valid_cpi(cpi_base):
        raise DeflateDeflatorValueError(
            f"CPI at base date {parameters.base_date.isoformat()} must be positive and finite"
        )

    output_key = deflated_column_key(target.key, deflator.key, parameters.base_date)
    if output_key in columns:
        raise DeflateColumnCollisionError(f"Derived column {output_key!r} already exists")

    values: list[float | None] = []
    for nominal, cpi in zip(target.values, deflator.values, strict=True):
        if cpi is not None and not _valid_cpi(cpi):
            raise DeflateDeflatorValueError(
                f"Deflator column {deflator.key!r} contains a non-positive or invalid CPI level"
            )
        if nominal is None or cpi is None:
            values.append(None)
            continue
        if type(nominal) not in {int, float} or not math.isfinite(nominal):
            raise DeflateCalculationError(
                f"Target column {target.key!r} contains an invalid nominal value"
            )
        try:
            real_value = nominal * cpi_base / cpi
        except ArithmeticError as exc:
            raise DeflateCalculationError(
                f"Deflation calculation failed for target column {target.key!r}"
            ) from exc
        if not math.isfinite(real_value):
            raise DeflateCalculationError(
                f"Deflation produced a non-finite value for target column {target.key!r}"
            )
        values.append(real_value)

    base_label = parameters.base_date.isoformat()
    derived = Column(
        key=output_key,
        label=f"{target.label} (real, {deflator.label}, {base_label} prices)",
        dtype="number",
        values=values,
        measure_type="stock",
        unit=target.unit,
        origin="derived",
        lineage=Lineage(
            parents=[
                ParentLineage(
                    frame_id=frame.frame_id,
                    frame_version=frame.version,
                    column_key=target.key,
                    lineage=target.lineage,
                ),
                ParentLineage(
                    frame_id=frame.frame_id,
                    frame_version=frame.version,
                    column_key=deflator.key,
                    lineage=deflator.lineage,
                ),
            ],
            transformations=[
                Transformation(
                    name=OperationType.DEFLATE_COLUMN.value,
                    implementation_version=TRANSFORMATION_VERSION,
                    parameters=[
                        MetadataEntry(
                            key="convention_reference",
                            value=parameters.convention_reference,
                        ),
                        MetadataEntry(key="base_date", value=base_label),
                        MetadataEntry(key="deflator_column_key", value=deflator.key),
                        MetadataEntry(key="formula", value="nominal_t * CPI_base / CPI_t"),
                    ],
                )
            ],
        ),
    )
    return AnalysisFrame.model_validate(
        {**frame.model_dump(), "columns": (*frame.columns, derived)}
    )
