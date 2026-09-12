from datetime import date

import pytest
from pydantic import ValidationError

from kkb_agent.frame import (
    AnalysisFrame,
    Finding,
    IndexColumnParameters,
    Operation,
    OperationType,
    Spine,
)


def operation(**overrides):
    return Operation(
        **{
            "operation_id": "op-a",
            "kind": "add_column",
            "parameters": {"series_reference": "catalog:series-a", "column_key": "a"},
            "timestamp": "2026-01-01T00:00:00Z",
            "source_version": 0,
            "resulting_version": 1,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "kind,parameters,source",
    [
        ("add_column", {"series_reference": "file:a", "column_key": "a"}, 0),
        (
            "deflate_column",
            {
                "column_key": "a",
                "deflator_column_key": "b",
                "base_date": "2025-01-01",
                "convention_reference": "cpi_base_period_constant_prices_v1",
            },
            0,
        ),
        ("index_column", {"column_key": "a", "base_date": "2025-01-01"}, 0),
        ("revert_to", {"target_version": 0}, 2),
    ],
)
def test_operation_serialization(kind, parameters, source):
    op = operation(
        kind=kind, parameters=parameters, source_version=source, resulting_version=source + 1
    )
    assert Operation.model_validate_json(op.model_dump_json()) == op
    assert OperationType(kind) == op.kind


@pytest.mark.parametrize("kind", ["python", "sql", "join", "index_columns", "reslice_spine"])
def test_closed_vocabulary(kind):
    with pytest.raises(ValidationError):
        operation(kind=kind)


@pytest.mark.parametrize(
    "overrides",
    [
        {"parameters": {"sql": "SELECT 1"}},
        {"parameters": {"series_reference": "a", "column_key": "a", "python": "pass"}},
        {"parameters": {"target_version": 0}},
        {"source_version": -1},
        {"resulting_version": 0},
        {"resulting_version": 2},
        {"timestamp": "2026-01-01T00:00:00"},
        {"kind": "revert_to", "parameters": {"target_version": 0}},
    ],
)
def test_invalid_operation(overrides):
    with pytest.raises(ValidationError):
        operation(**overrides)


def test_history_is_contiguous_and_immutable():
    first = operation()
    with pytest.raises(ValidationError):
        first.operation_id = "rewritten"
    for operations in [(operation(source_version=1, resulting_version=2),), (first, first)]:
        with pytest.raises(ValidationError):
            AnalysisFrame(
                frame_id="f", version=len(operations), spine=Spine(values=[]), operations=operations
            )


def test_successor_retains_history_and_spine():
    # Snapshots are deliberately empty: validating audit structure does not execute add_column.
    spine = Spine(values=[])
    prior = AnalysisFrame(frame_id="f", version=1, spine=spine, operations=[operation()])
    next_op = operation(operation_id="op-b", source_version=1, resulting_version=2)
    successor = AnalysisFrame(
        frame_id="f", version=2, spine=spine, operations=[*prior.operations, next_op]
    )
    successor.assert_successor_of(prior)
    rewritten = AnalysisFrame(
        **{
            **successor.model_dump(),
            "operations": [
                operation(operation_id="different"),
                next_op,
            ],
        }
    )
    with pytest.raises(ValueError, match="Historical operation"):
        rewritten.assert_successor_of(prior)
    with pytest.raises(ValueError):
        prior.assert_successor_of(successor)
    changed_spine = AnalysisFrame(**{**successor.model_dump(), "spine": {"values": ["2025-01-01"]}})
    with pytest.raises(ValueError, match="Spine"):
        changed_spine.assert_successor_of(prior)


def test_finding_revision_keeps_previous_evidence():
    earlier = Finding(
        finding_id="old",
        frame_version=0,
        statement="Earlier finding",
        supporting_column_keys=["historical-column"],
        producing_tool="tool",
    )
    prior = AnalysisFrame(
        frame_id="f",
        version=1,
        spine=Spine(values=[]),
        operations=[operation()],
        findings=[earlier],
    )
    revision = Finding(
        **{
            **earlier.model_dump(),
            "finding_id": "new",
            "supersedes": "old",
            "statement": "Revised conclusion",
        }
    )
    successor = AnalysisFrame(
        frame_id="f",
        version=2,
        spine=prior.spine,
        operations=[
            operation(),
            operation(operation_id="op-b", source_version=1, resulting_version=2),
        ],
        findings=[earlier, revision],
    )
    successor.assert_successor_of(prior)
    removed = AnalysisFrame(**{**successor.model_dump(), "findings": []})
    with pytest.raises(ValueError, match="Historical findings"):
        removed.assert_successor_of(prior)


def test_index_parameters_public_typed_frozen_and_serializable():
    params = IndexColumnParameters(column_key="a", base_date=date(2025, 1, 1))
    op = operation(kind="index_column", parameters=params)
    restored = Operation.model_validate_json(op.model_dump_json())
    assert isinstance(restored.parameters, IndexColumnParameters)
    assert restored.parameters.base_date == date(2025, 1, 1)
    assert set(params.model_dump()) == {"column_key", "base_date"}
    assert "IndexColumnParameters" in Operation.model_json_schema()["$defs"]
    with pytest.raises(ValidationError):
        params.column_key = "changed"


@pytest.mark.parametrize(
    "parameters",
    [
        {"column_key": "a"},
        {"base_date": "2025-01-01"},
        {"column_key": "", "base_date": "2025-01-01"},
        {"column_key": "a", "base_date": "2025-02-30"},
        {"column_key": "a", "base_date": None},
        {"column_key": "a", "base_date": "2025-01-01", "base_value": 1000},
        {"column_key": "a", "base_date": "2025-01-01", "python": "pass"},
        {"series_reference": "file:a", "column_key": "a"},
    ],
)
def test_index_rejects_invalid_parameters(parameters):
    with pytest.raises(ValidationError):
        operation(kind="index_column", parameters=parameters)


@pytest.mark.parametrize("kind", ["add_column", "deflate_column", "revert_to"])
def test_index_parameters_rejected_for_other_operations(kind):
    with pytest.raises(ValidationError, match="parameter schema"):
        operation(
            kind=kind,
            parameters=IndexColumnParameters(
                column_key="a",
                base_date=date(2025, 1, 1),
            ),
        )
