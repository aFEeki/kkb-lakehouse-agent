"""Public AnalysisFrame snapshot, evidence, and operation contracts."""

from kkb_agent.frame._base import MetadataEntry
from kkb_agent.frame.invariants import (
    ColumnAlignmentViolation,
    SpineViolation,
    assert_columns_aligned,
    assert_existing_columns_intact,
    assert_spine_intact,
)
from kkb_agent.frame.models import (
    AnalysisFrame,
    ChartSpec,
    Column,
    Finding,
    Lineage,
    ParentLineage,
    SourceReference,
    Spine,
    SpineRange,
    Transformation,
    Unit,
)
from kkb_agent.frame.operations import (
    AddColumnParameters,
    DeflateColumnParameters,
    IndexColumnParameters,
    Operation,
    OperationType,
    RevertToParameters,
)

__all__ = [
    "AddColumnParameters",
    "AnalysisFrame",
    "ChartSpec",
    "Column",
    "ColumnAlignmentViolation",
    "DeflateColumnParameters",
    "Finding",
    "IndexColumnParameters",
    "Lineage",
    "MetadataEntry",
    "Operation",
    "OperationType",
    "ParentLineage",
    "RevertToParameters",
    "SourceReference",
    "Spine",
    "SpineRange",
    "SpineViolation",
    "Transformation",
    "Unit",
    "assert_columns_aligned",
    "assert_existing_columns_intact",
    "assert_spine_intact",
]
