from datetime import UTC, date, datetime

import pytest

from kkb_agent.agent import OperationExecutor, create_operation_executor
from kkb_agent.agent.handlers import (
    IndexBasePeriodMissingError,
    IndexBasePeriodNotFoundError,
    IndexBaseValueZeroError,
    IndexCalculationError,
    IndexColumnCollisionError,
    IndexColumnError,
    IndexColumnNotFoundError,
    IndexColumnTypeError,
    IndexSpineTypeError,
    index_column_handler,
    indexed_column_key,
)
from kkb_agent.frame import (
    AnalysisFrame,
    ChartSpec,
    Column,
    Finding,
    Lineage,
    Operation,
    OperationType,
    SourceReference,
    Spine,
)

DATES = (date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1))


def source_column(values=(200, 220, 250), **changes) -> Column:
    fields = {
        "key": "loans",
        "label": "Housing loans",
        "dtype": "number",
        "values": values,
        "measure_type": "flow",
        "origin": "source",
        "lineage": Lineage(
            sources=[SourceReference(source_type="fixture", reference="fixture:loans")]
        ),
    }
    return Column(**(fields | changes))


def frame_with(column=None, **changes) -> AnalysisFrame:
    column = column or source_column()
    fields = {
        "frame_id": "frame-a",
        "spine": Spine(values=DATES),
        "columns": [column],
        "findings": [
            Finding(
                finding_id="finding-a",
                statement="Existing finding",
                frame_version=0,
                supporting_column_keys=[column.key],
                producing_tool="fixture",
            )
        ],
        "charts": [
            ChartSpec(
                chart_id="chart-a",
                chart_type="line",
                spine_key="time",
                column_keys=[column.key],
            )
        ],
    }
    return AnalysisFrame(**(fields | changes))


def operation(base_date=DATES[0], **changes) -> Operation:
    fields = {
        "operation_id": "op-index",
        "kind": "index_column",
        "parameters": {"column_key": "loans", "base_date": base_date},
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "source_version": 0,
        "resulting_version": 1,
    }
    return Operation(**(fields | changes))


def test_basic_rebase_retains_original_and_frame_state():
    frame = frame_with()
    candidate = index_column_handler(frame, operation())

    assert candidate.columns[0] is not frame.columns[0]
    assert candidate.columns[0] == frame.columns[0]
    assert candidate.columns[0].model_dump_json() == frame.columns[0].model_dump_json()
    assert candidate.columns[0].values == (200, 220, 250)
    assert candidate.columns[1].values == pytest.approx((100, 110, 125))
    assert candidate.columns[1].values[0] == 100
    assert candidate.columns[1].origin == "derived"
    assert candidate.columns[1].dtype == "number"
    assert candidate.columns[1].measure_type == "index"
    assert candidate.columns[1].unit.symbol == "index"
    assert candidate.spine == frame.spine
    assert candidate.frame_id == frame.frame_id
    assert candidate.version == frame.version == 0
    assert candidate.operations == frame.operations == ()
    assert candidate.findings == frame.findings
    assert candidate.charts == frame.charts
    assert frame.columns == (source_column(),)


def test_missing_non_base_values_remain_explicit():
    frame = frame_with(source_column(values=(200, None, 250)))
    indexed = index_column_handler(frame, operation()).columns[-1]
    assert indexed.values == (100, None, 125)
    assert indexed.missing_count == 1


def test_lineage_records_exact_parent_and_transformation():
    frame = frame_with()
    indexed = index_column_handler(frame, operation(DATES[1])).columns[-1]
    parent = indexed.lineage.parents[0]
    assert parent.frame_id == frame.frame_id
    assert parent.frame_version == frame.version
    assert parent.column_key == frame.columns[0].key
    assert parent.lineage == frame.columns[0].lineage
    transformation = indexed.lineage.transformations[0]
    assert transformation.name == "index_column"
    assert transformation.implementation_version == "1"
    assert [(item.key, item.value) for item in transformation.parameters] == [
        ("base_date", "2025-02-01"),
        ("base_value", 100),
    ]


def test_key_and_label_are_deterministic_and_explicit():
    first = index_column_handler(frame_with(), operation(DATES[1])).columns[-1]
    second = index_column_handler(frame_with(), operation(DATES[1])).columns[-1]
    assert first.key == second.key == "loans__index_2025-02-01"
    assert first.key == indexed_column_key("loans", DATES[1])
    assert "2025-02-01" in first.label
    assert "= 100" in first.label


def test_missing_base_period_never_chooses_neighbor():
    frame = frame_with()
    with pytest.raises(IndexBasePeriodNotFoundError, match="2025-01-02"):
        index_column_handler(frame, operation(date(2025, 1, 2)))
    assert frame.columns == (source_column(),)


def test_missing_base_value_fails_atomically():
    frame = frame_with(source_column(values=(None, 220, 250)))
    with pytest.raises(IndexBasePeriodMissingError, match="exact base date"):
        index_column_handler(frame, operation())
    assert frame.version == 0
    assert len(frame.columns) == 1


def test_zero_base_value_fails():
    with pytest.raises(IndexBaseValueZeroError, match="zero"):
        index_column_handler(frame_with(source_column(values=(0, 1, 2))), operation())


@pytest.mark.parametrize(
    "column",
    [
        source_column(dtype="string", values=("200", "220", "250")),
        source_column(dtype="boolean", values=(True, False, True)),
    ],
)
def test_non_numeric_columns_are_rejected(column):
    with pytest.raises(IndexColumnTypeError, match="non-numeric"):
        index_column_handler(frame_with(column), operation())


def test_missing_source_column_is_rejected():
    with pytest.raises(IndexColumnNotFoundError, match="absent"):
        index_column_handler(
            frame_with(), operation(parameters={"column_key": "absent", "base_date": DATES[0]})
        )


def test_output_key_collision_is_rejected_and_repeated_execution_fails():
    frame = frame_with()
    candidate = index_column_handler(frame, operation())
    with pytest.raises(IndexColumnCollisionError, match="already exists"):
        index_column_handler(candidate, operation())
    assert len(candidate.columns) == 2


def test_datetime_spine_is_rejected_as_ambiguous():
    frame = frame_with(
        spine=Spine(
            kind="datetime",
            values=[
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 2, 1, tzinfo=UTC),
                datetime(2025, 3, 1, tzinfo=UTC),
            ],
        )
    )
    with pytest.raises(IndexSpineTypeError, match="exact timestamp contract"):
        index_column_handler(frame, operation())


def test_wrong_operation_kind_is_rejected():
    wrong = Operation(
        operation_id="op-add",
        kind="add_column",
        parameters={"series_reference": "catalog:a", "column_key": "a"},
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source_version=0,
        resulting_version=1,
    )
    with pytest.raises(IndexColumnError, match="IndexColumnParameters"):
        index_column_handler(frame_with(), wrong)


def test_non_finite_result_is_rejected():
    frame = frame_with(source_column(values=(1e-320, 1e308, 1.0)))
    with pytest.raises(IndexCalculationError, match="non-finite"):
        index_column_handler(frame, operation())


def test_end_to_end_through_production_executor():
    frame = frame_with()
    requested = operation()
    executor = create_operation_executor()
    assert executor.supported_operations == frozenset({OperationType.INDEX_COLUMN})

    result = executor.execute(frame, requested)

    assert result.version == 1
    assert result.operations == (requested,)
    assert result.columns[:-1] == frame.columns
    assert result.spine == frame.spine
    assert frame.version == 0
    assert frame.operations == ()
    assert len(frame.columns) == 1


def test_failed_execution_is_atomic():
    frame = frame_with(source_column(values=(None, 220, 250)))
    executor = create_operation_executor()
    with pytest.raises(IndexBasePeriodMissingError):
        executor.execute(frame, operation())
    assert frame.version == 0
    assert frame.operations == ()
    assert len(frame.columns) == 1


def test_only_index_handler_is_registered_in_production_composition():
    executor = create_operation_executor()
    assert isinstance(executor, OperationExecutor)
    assert executor.supported_operations == frozenset({OperationType.INDEX_COLUMN})
