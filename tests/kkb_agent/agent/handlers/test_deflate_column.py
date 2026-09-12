from datetime import UTC, date, datetime

import pytest

from kkb_agent.agent import create_operation_executor
from kkb_agent.agent.handlers import (
    DEFLATION_CONVENTION,
    DeflateBasePeriodMissingError,
    DeflateBasePeriodNotFoundError,
    DeflateCalculationError,
    DeflateColumnCollisionError,
    DeflateColumnNotFoundError,
    DeflateColumnTypeError,
    DeflateConventionError,
    DeflateDeflatorMeasureError,
    DeflateDeflatorValueError,
    DeflateSpineTypeError,
    DeflateTargetMeasureError,
    deflate_column_handler,
    deflated_column_key,
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
    Unit,
)

DATES = (date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1))


def source_column(key, label, values, measure_type, unit=None, dtype="number") -> Column:
    return Column(
        key=key,
        label=label,
        dtype=dtype,
        values=values,
        measure_type=measure_type,
        unit=unit,
        origin="source",
        lineage=Lineage(
            sources=[SourceReference(source_type="fixture", reference=f"fixture:{key}")]
        ),
    )


def nominal(values=(1000, 1200, 1500), **changes) -> Column:
    fields = {
        "key": "loans",
        "label": "Housing loan stock",
        "values": values,
        "measure_type": "stock",
        "unit": Unit(symbol="TRY", scale=1_000_000, description="million TRY"),
    }
    return source_column(**(fields | changes))


def cpi(values=(100, 120, 150), **changes) -> Column:
    fields = {
        "key": "cpi",
        "label": "Headline CPI",
        "values": values,
        "measure_type": "index",
        "unit": Unit(symbol="index", description="headline CPI price level"),
    }
    return source_column(**(fields | changes))


def frame_with(columns=None, **changes) -> AnalysisFrame:
    columns = columns or [nominal(), cpi()]
    fields = {
        "frame_id": "frame-deflate",
        "spine": Spine(values=DATES),
        "columns": columns,
        "findings": [
            Finding(
                finding_id="finding-a",
                statement="Existing finding",
                frame_version=0,
                supporting_column_keys=[columns[0].key],
                producing_tool="fixture",
            )
        ],
        "charts": [
            ChartSpec(
                chart_id="chart-a",
                chart_type="line",
                spine_key="time",
                column_keys=[column.key for column in columns],
            )
        ],
    }
    return AnalysisFrame(**(fields | changes))


def operation(base_date=DATES[0], **changes) -> Operation:
    fields = {
        "operation_id": "op-deflate",
        "kind": "deflate_column",
        "parameters": {
            "column_key": "loans",
            "deflator_column_key": "cpi",
            "base_date": base_date,
            "convention_reference": DEFLATION_CONVENTION,
        },
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "source_version": 0,
        "resulting_version": 1,
    }
    return Operation(**(fields | changes))


def test_correct_deflation_changes_only_appended_column():
    other = source_column("other", "Other stock", (4, 5, 6), "stock")
    frame = frame_with([nominal(), cpi(), other])

    candidate = deflate_column_handler(frame, operation(DATES[1]))

    assert candidate.columns[:-1] == frame.columns
    assert candidate.columns[0].values == (1000, 1200, 1500)
    assert candidate.columns[1] == frame.columns[1]
    assert candidate.columns[2] == other
    assert candidate.columns[-1].values == pytest.approx((1200, 1200, 1200))
    assert candidate.columns[-1].values[1] == frame.columns[0].values[1]
    assert candidate.columns[-1].measure_type == "stock"
    assert candidate.columns[-1].unit == frame.columns[0].unit
    assert candidate.spine == frame.spine
    assert candidate.findings == frame.findings
    assert candidate.charts == frame.charts
    assert candidate.frame_id == frame.frame_id
    assert candidate.version == frame.version == 0
    assert candidate.operations == frame.operations == ()


def test_missing_target_or_cpi_observations_remain_missing():
    frame = frame_with([nominal(values=(1000, None, 1500)), cpi(values=(100, 120, None))])
    derived = deflate_column_handler(frame, operation()).columns[-1]
    assert derived.values == (1000, None, None)
    assert derived.missing_count == 2


def test_exact_missing_base_date_is_rejected_without_neighbor_selection():
    with pytest.raises(DeflateBasePeriodNotFoundError, match="2025-01-02"):
        deflate_column_handler(frame_with(), operation(date(2025, 1, 2)))


def test_missing_base_cpi_is_rejected():
    with pytest.raises(DeflateBasePeriodMissingError, match="exact base date"):
        deflate_column_handler(frame_with([nominal(), cpi(values=(None, 120, 150))]), operation())


@pytest.mark.parametrize("bad_cpi", [0, -1])
def test_non_positive_cpi_is_rejected(bad_cpi):
    with pytest.raises(DeflateDeflatorValueError, match="non-positive|positive and finite"):
        deflate_column_handler(
            frame_with([nominal(), cpi(values=(100, bad_cpi, 150))]), operation()
        )


@pytest.mark.parametrize("measure_type", ["rate", "index", "flow", None])
def test_target_must_be_a_monetary_stock(measure_type):
    with pytest.raises(DeflateTargetMeasureError, match="measure_type 'stock'"):
        deflate_column_handler(frame_with([nominal(measure_type=measure_type), cpi()]), operation())


@pytest.mark.parametrize("measure_type", ["rate", "percentage_change", "stock", None])
def test_deflator_must_be_a_price_level_index(measure_type):
    with pytest.raises(DeflateDeflatorMeasureError, match="price-level index"):
        deflate_column_handler(frame_with([nominal(), cpi(measure_type=measure_type)]), operation())


def test_non_numeric_target_and_deflator_are_rejected():
    with pytest.raises(DeflateColumnTypeError, match="Target.*non-numeric"):
        deflate_column_handler(
            frame_with([nominal(dtype="string", values=("a", "b", "c")), cpi()]), operation()
        )
    with pytest.raises(DeflateColumnTypeError, match="Deflator.*non-numeric"):
        deflate_column_handler(
            frame_with([nominal(), cpi(dtype="string", values=("a", "b", "c"))]), operation()
        )


@pytest.mark.parametrize("missing_key", ["loans", "cpi"])
def test_missing_requested_column_is_rejected(missing_key):
    columns = [column for column in [nominal(), cpi()] if column.key != missing_key]
    with pytest.raises(DeflateColumnNotFoundError, match=missing_key):
        deflate_column_handler(frame_with(columns), operation())


def test_wrong_convention_is_rejected():
    parameters = operation().parameters.model_dump() | {"convention_reference": "other-v1"}
    with pytest.raises(DeflateConventionError, match="Unsupported"):
        deflate_column_handler(frame_with(), operation(parameters=parameters))


def test_output_key_collision_is_rejected():
    candidate = deflate_column_handler(frame_with(), operation())
    with pytest.raises(DeflateColumnCollisionError, match="already exists"):
        deflate_column_handler(candidate, operation())


def test_output_key_label_and_lineage_are_deterministic():
    frame = frame_with()
    derived = deflate_column_handler(frame, operation(DATES[1])).columns[-1]
    assert derived.key == "loans__deflated_by_cpi_2025-02-01"
    assert derived.key == deflated_column_key("loans", "cpi", DATES[1])
    assert derived.label == "Housing loan stock (real, Headline CPI, 2025-02-01 prices)"
    assert [
        (parent.column_key, parent.frame_id, parent.frame_version)
        for parent in derived.lineage.parents
    ] == [
        ("loans", frame.frame_id, frame.version),
        ("cpi", frame.frame_id, frame.version),
    ]
    assert derived.lineage.parents[0].lineage == frame.columns[0].lineage
    assert derived.lineage.parents[1].lineage == frame.columns[1].lineage
    transformation = derived.lineage.transformations[0]
    assert transformation.name == "deflate_column"
    assert transformation.implementation_version == "1"
    assert [(entry.key, entry.value) for entry in transformation.parameters] == [
        ("convention_reference", DEFLATION_CONVENTION),
        ("base_date", "2025-02-01"),
        ("deflator_column_key", "cpi"),
        ("formula", "nominal_t * CPI_base / CPI_t"),
    ]


def test_datetime_spine_is_rejected_like_index_column():
    frame = frame_with(
        spine=Spine(
            kind="datetime",
            values=[datetime(2025, month, 1, tzinfo=UTC) for month in range(1, 4)],
        )
    )
    with pytest.raises(DeflateSpineTypeError, match="exact timestamp contract"):
        deflate_column_handler(frame, operation())


def test_non_finite_result_is_rejected():
    frame = frame_with([nominal(values=(1e308, 1e308, 1e308)), cpi(values=(1e308, 1e-320, 1.0))])
    with pytest.raises(DeflateCalculationError, match="non-finite"):
        deflate_column_handler(frame, operation())


def test_successful_executor_execution_commits_exactly_once():
    frame = frame_with()
    requested = operation()
    executor = create_operation_executor()
    assert executor.supported_operations == frozenset(
        {OperationType.DEFLATE_COLUMN, OperationType.INDEX_COLUMN}
    )

    result = executor.execute(frame, requested)

    assert result.version == 1
    assert result.operations == (requested,)
    assert len(result.columns) == len(frame.columns) + 1
    assert frame.version == 0
    assert frame.operations == ()
    assert len(frame.columns) == 2


def test_failed_executor_execution_is_atomic():
    frame = frame_with([nominal(), cpi(values=(None, 120, 150))])
    with pytest.raises(DeflateBasePeriodMissingError):
        create_operation_executor().execute(frame, operation())
    assert frame.version == 0
    assert frame.operations == ()
    assert len(frame.columns) == 2
