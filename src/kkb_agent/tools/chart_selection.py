"""Deterministic chart and axis selection for an AnalysisFrame."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from statistics import median
from typing import Literal

from pydantic import Field, model_validator

from kkb_agent.frame import AnalysisFrame, AxisAssignment, ChartSpec, Column
from kkb_agent.frame._base import Contract

DEFAULT_MAGNITUDE_THRESHOLD = 100.0


class ChartSelectionRefusalReason(StrEnum):
    NO_NUMERIC_COLUMNS = "no_numeric_columns"
    UNKNOWN_COLUMN = "unknown_column"
    NON_NUMERIC_COLUMN = "non_numeric_column"
    MISSING_UNIT = "missing_unit"
    MISSING_UNIT_SYMBOL = "missing_unit_symbol"
    MISSING_MEASURE_TYPE = "missing_measure_type"
    TOO_MANY_UNIT_GROUPS = "too_many_unit_groups"
    INVALID_OVERRIDE = "invalid_override"


class ChartSelectionError(ValueError):
    """Typed refusal when a deterministic chart cannot be selected safely."""

    def __init__(
        self,
        reason: ChartSelectionRefusalReason,
        message: str,
        *,
        column_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.column_keys = tuple(column_keys)


class ChartSelectionOverride(Contract):
    """Validated contract-level changes to the deterministic default."""

    chart_type: Literal["line", "bar", "scatter"] | None = None
    axis_assignments: tuple[AxisAssignment, ...] | None = None
    title: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_a_change(self):
        if self.chart_type is None and self.axis_assignments is None and self.title is None:
            raise ValueError("Override must change at least one chart field")
        return self


@dataclass(frozen=True)
class UnitGroup:
    symbol: str
    scale: float
    measure_type: str


@dataclass(frozen=True)
class ChartSelectionResult:
    spec: ChartSpec
    unit_groups: tuple[UnitGroup, ...]
    magnitude_ratio: float | None
    magnitude_threshold: float


def select_chart(
    frame: AnalysisFrame,
    *,
    column_keys: Sequence[str] | None = None,
    override: ChartSelectionOverride | None = None,
) -> ChartSelectionResult:
    """Build the same explicit chart spec for the same frame and parameters."""

    columns = _select_columns(frame, column_keys)
    groups = _unit_groups(columns)
    default_assignments = _default_axis_assignments(columns, groups)
    assignments = _override_assignments(columns, override, default_assignments)
    magnitude_ratio = _magnitude_ratio(columns)
    indexing_recommended = (
        magnitude_ratio is not None and magnitude_ratio >= DEFAULT_MAGNITUDE_THRESHOLD
    )
    chart_type = override.chart_type if override and override.chart_type else "line"
    title = override.title if override else None
    chart_id = _chart_id(
        frame,
        columns=columns,
        chart_type=chart_type,
        assignments=assignments,
        indexing_recommended=indexing_recommended,
        title=title,
        magnitude_threshold=DEFAULT_MAGNITUDE_THRESHOLD,
    )

    return ChartSelectionResult(
        spec=ChartSpec(
            chart_id=chart_id,
            chart_type=chart_type,
            spine_key=frame.spine.key,
            column_keys=tuple(column.key for column in columns),
            axis_assignments=assignments,
            indexing_recommended=indexing_recommended,
            title=title,
        ),
        unit_groups=groups,
        magnitude_ratio=magnitude_ratio,
        magnitude_threshold=DEFAULT_MAGNITUDE_THRESHOLD,
    )


def _select_columns(frame: AnalysisFrame, column_keys: Sequence[str] | None) -> tuple[Column, ...]:
    by_key = {column.key: column for column in frame.columns}
    if column_keys is None:
        selected = tuple(
            column for column in frame.columns if column.dtype in {"integer", "number"}
        )
    else:
        keys = tuple(column_keys)
        if len(keys) != len(set(keys)):
            raise ChartSelectionError(
                ChartSelectionRefusalReason.INVALID_OVERRIDE,
                "column_keys must not contain duplicates",
                column_keys=keys,
            )
        unknown = tuple(key for key in keys if key not in by_key)
        if unknown:
            raise ChartSelectionError(
                ChartSelectionRefusalReason.UNKNOWN_COLUMN,
                "Requested chart columns are not present in the frame",
                column_keys=unknown,
            )
        requested = set(keys)
        selected = tuple(column for column in frame.columns if column.key in requested)

    if not selected:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.NO_NUMERIC_COLUMNS,
            "The frame has no numeric columns to chart",
        )
    non_numeric = tuple(
        column.key for column in selected if column.dtype not in {"integer", "number"}
    )
    if non_numeric:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.NON_NUMERIC_COLUMN,
            "Chart columns must be numeric",
            column_keys=non_numeric,
        )
    return selected


def _group_for(column: Column) -> UnitGroup:
    if column.unit is None:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.MISSING_UNIT,
            "Chart columns require explicit unit metadata",
            column_keys=(column.key,),
        )
    if column.unit.symbol is None:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.MISSING_UNIT_SYMBOL,
            "Chart columns require an explicit unit symbol",
            column_keys=(column.key,),
        )
    if column.measure_type is None:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.MISSING_MEASURE_TYPE,
            "Chart columns require an explicit measure type",
            column_keys=(column.key,),
        )
    return UnitGroup(
        symbol=column.unit.symbol,
        scale=column.unit.scale,
        measure_type=column.measure_type,
    )


def _unit_groups(columns: tuple[Column, ...]) -> tuple[UnitGroup, ...]:
    groups: list[UnitGroup] = []
    for column in columns:
        group = _group_for(column)
        if group not in groups:
            groups.append(group)
    if len(groups) > 2:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.TOO_MANY_UNIT_GROUPS,
            "A dual-axis chart cannot represent more than two incompatible unit groups",
            column_keys=tuple(column.key for column in columns),
        )
    return tuple(groups)


def _default_axis_assignments(
    columns: tuple[Column, ...], groups: tuple[UnitGroup, ...]
) -> tuple[AxisAssignment, ...]:
    axes = {group: ("left" if index == 0 else "right") for index, group in enumerate(groups)}
    return tuple(
        AxisAssignment(column_key=column.key, axis=axes[_group_for(column)]) for column in columns
    )


def _override_assignments(
    columns: tuple[Column, ...],
    override: ChartSelectionOverride | None,
    defaults: tuple[AxisAssignment, ...],
) -> tuple[AxisAssignment, ...]:
    if override is None or override.axis_assignments is None:
        return defaults
    expected = tuple(column.key for column in columns)
    actual = tuple(item.column_key for item in override.axis_assignments)
    if actual != expected:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.INVALID_OVERRIDE,
            "Override axis assignments must reference every selected column "
            "exactly once and in order",
            column_keys=actual,
        )
    used_axes = {item.axis for item in override.axis_assignments}
    if "right" in used_axes and "left" not in used_axes:
        raise ChartSelectionError(
            ChartSelectionRefusalReason.INVALID_OVERRIDE,
            "Override cannot use a right axis without a left axis",
            column_keys=actual,
        )
    return override.axis_assignments


def _magnitude_ratio(columns: tuple[Column, ...]) -> float | None:
    representatives: list[float] = []
    for column in columns:
        magnitudes = [
            abs(float(value))
            for value in column.values
            if value is not None and not isinstance(value, bool) and float(value) != 0.0
        ]
        if magnitudes:
            representatives.append(float(median(magnitudes)))
    if len(representatives) < 2:
        return None
    return max(representatives) / min(representatives)


def _chart_id(
    frame: AnalysisFrame,
    *,
    columns: tuple[Column, ...],
    chart_type: str,
    assignments: tuple[AxisAssignment, ...],
    indexing_recommended: bool,
    title: str | None,
    magnitude_threshold: float,
) -> str:
    identity = json.dumps(
        {
            "frame_id": frame.frame_id,
            "frame_version": frame.version,
            "spine_key": frame.spine.key,
            "chart_type": chart_type,
            "column_keys": [column.key for column in columns],
            "axis_assignments": [item.model_dump(mode="json") for item in assignments],
            "indexing_recommended": indexing_recommended,
            "magnitude_threshold": format(magnitude_threshold, ".17g"),
            "title": title,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"chart-{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
