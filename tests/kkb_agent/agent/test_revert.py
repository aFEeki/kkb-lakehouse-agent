from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from kkb_agent.agent import (
    FrameSnapshotHistory,
    SnapshotLineageError,
    SnapshotNotFoundError,
    create_finding,
    create_operation_executor,
)
from kkb_agent.agent.handlers import DEFLATION_CONVENTION
from kkb_agent.frame import (
    AnalysisFrame,
    ChartSpec,
    Column,
    Lineage,
    Operation,
    SourceReference,
    Spine,
    SpineRange,
    Unit,
)

DATES = (date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1))


def source_column(key, label, values, measure_type, unit) -> Column:
    return Column(
        key=key,
        label=label,
        dtype="number",
        values=values,
        measure_type=measure_type,
        unit=unit,
        origin="source",
        lineage=Lineage(
            sources=[SourceReference(source_type="fixture", reference=f"fixture:{key}")]
        ),
    )


def initial_frame() -> AnalysisFrame:
    return AnalysisFrame(
        frame_id="frame-revert",
        spine=Spine(values=DATES),
        columns=[
            source_column(
                "loans",
                "Housing loan stock",
                (1000, 1200, 1500),
                "stock",
                Unit(symbol="TRY", scale=1_000_000),
            ),
            source_column(
                "cpi",
                "Headline CPI",
                (100, 120, 150),
                "index",
                Unit(symbol="index"),
            ),
        ],
        charts=[
            ChartSpec(
                chart_id="chart-base",
                chart_type="line",
                spine_key="time",
                column_keys=["loans", "cpi"],
            )
        ],
    )


def operation(kind: str, source_version: int, **parameters) -> Operation:
    return Operation(
        operation_id=f"op-{kind}-{source_version}",
        kind=kind,
        parameters=parameters,
        timestamp=datetime(2026, 9, 12, source_version, tzinfo=UTC),
        source_version=source_version,
        resulting_version=source_version + 1,
    )


def build_v2():
    history = FrameSnapshotHistory()
    executor = create_operation_executor(history)
    v0 = initial_frame()
    index = operation("index_column", 0, column_key="loans", base_date=DATES[0])
    v1 = executor.execute(v0, index)
    v1_with_finding = create_finding(
        v1,
        finding_id="finding-v1",
        statement="Indexed loans rose.",
        supporting_column_keys=["loans__index_2025-01-01"],
        spine_range=SpineRange(start=0, stop=3),
        producing_tool="index_column",
    )
    deflate = operation(
        "deflate_column",
        1,
        column_key="loans",
        deflator_column_key="cpi",
        base_date=DATES[0],
        convention_reference=DEFLATION_CONVENTION,
    )
    v2 = executor.execute(v1_with_finding, deflate)
    v2_with_finding = create_finding(
        v2,
        finding_id="finding-v2",
        statement="Real loans were flat.",
        supporting_column_keys=["loans__deflated_by_cpi_2025-01-01"],
        producing_tool="deflate_column",
    )
    return executor, history, v0, v1_with_finding, v2_with_finding, index, deflate


def test_normal_operations_retain_immutable_versions():
    executor, history, v0, v1, v2, _, _ = build_v2()

    assert executor.snapshot_history is history
    assert history.versions(v0.frame_id) == (0, 1, 2)
    target = history.resolve(v2, 1)
    assert target.columns == v1.columns
    assert target.charts == v1.charts
    assert target.spine == v1.spine
    assert v0.version == 0 and len(v0.columns) == 2
    assert v1.version == 1 and len(v1.columns) == 3
    assert v2.version == 2 and len(v2.columns) == 4


def test_revert_v2_to_v1_restores_state_and_preserves_audit_history():
    executor, history, v0, v1, v2, index, deflate = build_v2()
    revert = operation("revert_to", 2, target_version=1)

    v3 = executor.execute(v2, revert)

    assert v3.frame_id == v2.frame_id
    assert v3.version == 3
    assert v3.spine == v1.spine
    assert v3.columns == v1.columns
    assert v3.charts == v1.charts
    assert v3.findings == v2.findings
    assert [finding.frame_version for finding in v3.findings] == [1, 2]
    assert v3.operations == (index, deflate, revert)
    assert [item.timestamp for item in v3.operations] == [
        index.timestamp,
        deflate.timestamp,
        revert.timestamp,
    ]
    assert history.versions(v0.frame_id) == (0, 1, 2, 3)
    assert v0.version == 0 and len(v0.columns) == 2
    assert v1.version == 1 and len(v1.columns) == 3
    assert v2.version == 2 and len(v2.columns) == 4


def test_revert_after_revert_is_deterministic_and_keeps_full_log():
    executor, history, _, v1, v2, index, deflate = build_v2()
    revert_to_v1 = operation("revert_to", 2, target_version=1)
    v3 = executor.execute(v2, revert_to_v1)
    revert_to_v2 = operation("revert_to", 3, target_version=2)
    v4 = executor.execute(v3, revert_to_v2)
    revert_to_v1_again = operation("revert_to", 4, target_version=1)
    v5 = executor.execute(v4, revert_to_v1_again)

    assert v4.columns == v2.columns
    assert v4.charts == v2.charts
    assert v5.columns == v1.columns
    assert v5.charts == v1.charts
    assert v5.findings == v2.findings
    assert v5.operations == (
        index,
        deflate,
        revert_to_v1,
        revert_to_v2,
        revert_to_v1_again,
    )
    assert v5.version == 5
    assert history.versions(v5.frame_id) == (0, 1, 2, 3, 4, 5)


def test_unknown_target_fails_atomically_without_retaining_failed_transition():
    _, _, _, _, v2, _, _ = build_v2()
    empty_history = FrameSnapshotHistory()
    executor = create_operation_executor(empty_history)
    revert = operation("revert_to", 2, target_version=1)

    with pytest.raises(SnapshotNotFoundError, match="was not retained"):
        executor.execute(v2, revert)

    assert empty_history.versions(v2.frame_id) == ()
    assert v2.version == 2
    assert len(v2.operations) == 2
    assert len(v2.columns) == 4


def test_future_or_current_revert_target_is_rejected_by_frozen_contract():
    for target in (2, 3):
        with pytest.raises(ValidationError, match="Revert target must precede"):
            operation("revert_to", 2, target_version=target)


def test_retained_target_from_a_different_operation_branch_is_rejected():
    _, history, _, _, v2, _, _ = build_v2()
    unrelated_operations = [
        Operation(**(item.model_dump() | {"operation_id": f"unrelated-{item.operation_id}"}))
        for item in v2.operations
    ]
    unrelated_operations.append(operation("revert_to", 2, target_version=0))
    unrelated = AnalysisFrame(
        **(
            v2.model_dump()
            | {
                "version": 3,
                "operations": unrelated_operations,
            }
        )
    )
    history.retain(unrelated)

    with pytest.raises(SnapshotLineageError, match="not in the current operation history"):
        history.resolve(unrelated, 1)
