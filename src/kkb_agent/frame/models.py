"""Versioned analysis snapshots with explicit provenance and evidence."""

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationInfo, field_validator, model_validator

from kkb_agent.frame._base import Contract, Identifier, MetadataEntry, Scalar, Timestamp, Version
from kkb_agent.frame.invariants import assert_columns_aligned, assert_spine_intact
from kkb_agent.frame.operations import Operation


class Spine(Contract):
    key: Identifier = "time"
    kind: Literal["date", "datetime"] = "date"
    label: str | None = None
    values: tuple[date | AwareDatetime, ...]

    @field_validator("values", mode="before")
    @classmethod
    def parse_temporal_values(cls, values, info: ValidationInfo):
        if not isinstance(values, (tuple, list)):
            raise ValueError("Spine values must be an ordered sequence")
        kind = info.data.get("kind", "date")
        parsed = []
        for value in values:
            if isinstance(value, str):
                value = (
                    date.fromisoformat(value) if kind == "date" else datetime.fromisoformat(value)
                )
            if kind == "date" and type(value) is not date:
                raise ValueError("Date spine requires dates, not timestamps or numeric epochs")
            if kind == "datetime":
                if not isinstance(value, datetime) or value.utcoffset() is None:
                    raise ValueError("Datetime spine requires timezone-aware timestamps")
                value = value.astimezone(UTC)
            parsed.append(value)
        return tuple(parsed)

    @model_validator(mode="after")
    def unique_values(self):
        if len(set(self.values)) != len(self.values):
            raise ValueError("Duplicate spine values are forbidden")
        return self


class Unit(Contract):
    symbol: Identifier | None = None
    scale: float = Field(default=1.0, gt=0, strict=True)
    description: str | None = None


class SourceReference(Contract):
    source_type: Identifier
    reference: Identifier
    retrieved_at: Timestamp | None = None
    raw_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metadata: tuple[MetadataEntry, ...] = ()


class Transformation(Contract):
    """Descriptive audit record; name and parameters are never evaluated as code."""

    name: Identifier
    implementation_version: Identifier
    parameters: tuple[MetadataEntry, ...] = ()


class ParentLineage(Contract):
    frame_id: Identifier
    frame_version: Version
    column_key: Identifier
    lineage: "Lineage"


class Lineage(Contract):
    sources: tuple[SourceReference, ...] = ()
    parents: tuple[ParentLineage, ...] = ()
    transformations: tuple[Transformation, ...] = ()

    @model_validator(mode="after")
    def require_origin(self):
        if not self.sources and not self.parents:
            raise ValueError("Lineage must contain a source or a parent lineage snapshot")
        return self


class Column(Contract):
    key: Identifier
    label: Identifier
    dtype: Literal["integer", "number", "string", "boolean"]
    values: tuple[Scalar, ...]
    measure_type: Identifier | None = None
    unit: Unit | None = None
    origin: Literal["source", "derived"]
    lineage: Lineage

    @property
    def missing_count(self) -> int:
        return sum(value is None for value in self.values)

    @model_validator(mode="after")
    def validate_values_and_origin(self):
        types = {"integer": (int,), "number": (int, float), "string": (str,), "boolean": (bool,)}
        if any(value is not None and type(value) not in types[self.dtype] for value in self.values):
            raise ValueError("Column values do not match dtype; implicit coercion is forbidden")
        if self.origin == "source" and (not self.lineage.sources or self.lineage.parents):
            raise ValueError("Source columns require direct source lineage without parents")
        if self.origin == "derived" and (
            not self.lineage.parents or not self.lineage.transformations
        ):
            raise ValueError("Derived columns require parent lineage and a transformation chain")
        return self


class SpineRange(Contract):
    """Half-open row interval [start, stop) in the finding's evidence version."""

    start: Version
    stop: Version

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.stop <= self.start:
            raise ValueError("Evidence range must be nonempty and ordered")
        return self


class Finding(Contract):
    finding_id: Identifier
    statement: Identifier
    frame_version: Version
    supporting_column_keys: tuple[Identifier, ...] = Field(min_length=1)
    spine_range: SpineRange | None = None
    producing_tool: Identifier
    status: Literal["active", "superseded", "withdrawn"] = "active"
    supersedes: Identifier | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)
    caveats: tuple[str, ...] = ()


class AxisAssignment(Contract):
    column_key: Identifier
    axis: Literal["left", "right"]


class ChartSpec(Contract):
    chart_id: Identifier
    chart_type: Literal["line", "bar", "scatter"]
    spine_key: Identifier
    column_keys: tuple[Identifier, ...] = Field(min_length=1)
    axis_policy: Literal["by_unit"] = "by_unit"
    axis_assignments: tuple[AxisAssignment, ...] = ()
    indexing_recommended: bool = False
    title: str | None = None

    @model_validator(mode="after")
    def validate_axis_assignments(self):
        if self.axis_assignments:
            assigned_keys = tuple(item.column_key for item in self.axis_assignments)
            if assigned_keys != self.column_keys:
                raise ValueError(
                    "Axis assignments must reference every chart column exactly once and in order"
                )
        return self


class AnalysisFrame(Contract):
    frame_id: Identifier
    version: Version = 0
    spine: Spine
    columns: tuple[Column, ...] = ()
    findings: tuple[Finding, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    operations: tuple[Operation, ...] = ()

    @model_validator(mode="after")
    def structural_consistency(self):
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate column keys are forbidden")
        assert_columns_aligned(self.spine, self.columns)
        if len(self.operations) != self.version:
            raise ValueError("Version must equal the complete operation history length")
        ids = [op.operation_id for op in self.operations]
        if len(set(ids)) != len(ids):
            raise ValueError("Operation IDs must be unique")
        for version, op in enumerate(self.operations):
            if op.source_version != version:
                raise ValueError("Operation history must be contiguous from version zero")
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("Finding IDs must be unique")
        seen = set()
        for finding in self.findings:
            if finding.frame_version > self.version:
                raise ValueError("Finding cannot reference a future version")
            if finding.supersedes is not None and finding.supersedes not in seen:
                raise ValueError("Superseded finding must precede its revision")
            seen.add(finding.finding_id)
            if finding.frame_version == self.version:
                if not set(finding.supporting_column_keys) <= set(keys):
                    raise ValueError("Finding references an unknown current column")
                if finding.spine_range and finding.spine_range.stop > len(self.spine.values):
                    raise ValueError("Finding range exceeds the current spine")
        chart_ids = [chart.chart_id for chart in self.charts]
        if len(set(chart_ids)) != len(chart_ids):
            raise ValueError("Chart IDs must be unique")
        for chart in self.charts:
            if chart.spine_key != self.spine.key or not set(chart.column_keys) <= set(keys):
                raise ValueError("Chart references an unknown spine or column")
        return self

    def assert_successor_of(self, previous: "AnalysisFrame") -> None:
        """Validate append-only history at a future executor boundary; execute nothing."""
        if self.frame_id != previous.frame_id or self.version != previous.version + 1:
            raise ValueError("Successor must retain frame ID and advance exactly one version")
        assert_spine_intact(previous.spine, self.spine)
        if self.operations[:-1] != previous.operations:
            raise ValueError("Historical operation records cannot be rewritten")
        if self.findings[: len(previous.findings)] != previous.findings:
            raise ValueError("Historical findings cannot be rewritten or removed")
