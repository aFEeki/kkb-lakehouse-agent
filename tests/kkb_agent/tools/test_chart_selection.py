from datetime import date

import pytest
from pydantic import ValidationError

from kkb_agent.frame import (
    AnalysisFrame,
    AxisAssignment,
    Column,
    Lineage,
    SourceReference,
    Spine,
    Unit,
)
from kkb_agent.tools import (
    DEFAULT_MAGNITUDE_THRESHOLD,
    ChartSelectionError,
    ChartSelectionOverride,
    ChartSelectionRefusalReason,
    select_chart,
)


def _column(
    key: str,
    values=(1.0, 2.0, 3.0, 4.0),
    *,
    symbol: str | None = "TRY",
    scale: float = 1.0,
    measure_type: str | None = "stock",
    unit_present: bool = True,
    dtype: str = "number",
) -> Column:
    unit = Unit(symbol=symbol, scale=scale) if unit_present else None
    return Column(
        key=key,
        label=key,
        dtype=dtype,
        values=values,
        measure_type=measure_type,
        unit=unit,
        origin="source",
        lineage=Lineage(sources=(SourceReference(source_type="test", reference=key),)),
    )


def _frame(*columns: Column) -> AnalysisFrame:
    return AnalysisFrame(
        frame_id="frame-chart",
        spine=Spine(
            values=(
                date(2025, 1, 1),
                date(2025, 2, 1),
                date(2025, 3, 1),
                date(2025, 4, 1),
            )
        ),
        columns=columns,
    )


def test_default_selection_is_deterministic_and_preserves_frame_insertion_order():
    frame = _frame(
        _column("loans"),
        _column("rate", symbol="percent", measure_type="rate"),
        _column("deposits"),
    )

    first = select_chart(frame, column_keys=("deposits", "rate", "loans"))
    second = select_chart(frame, column_keys=("deposits", "rate", "loans"))

    assert first == second
    assert first.spec.chart_id == "chart-0a4e72add074372c"
    assert first.spec.column_keys == ("loans", "rate", "deposits")
    assert first.spec.axis_assignments == (
        AxisAssignment(column_key="loans", axis="left"),
        AxisAssignment(column_key="rate", axis="right"),
        AxisAssignment(column_key="deposits", axis="left"),
    )


def test_measure_type_is_part_of_axis_group_identity():
    result = select_chart(
        _frame(
            _column("stock", symbol="TRY", measure_type="stock"),
            _column("flow", symbol="TRY", measure_type="flow"),
        )
    )

    assert [assignment.axis for assignment in result.spec.axis_assignments] == [
        "left",
        "right",
    ]
    assert [group.measure_type for group in result.unit_groups] == ["stock", "flow"]


def test_unit_scale_is_part_of_axis_group_identity():
    result = select_chart(
        _frame(
            _column("try", symbol="TRY", scale=1.0),
            _column("million_try", symbol="TRY", scale=1_000_000.0),
        )
    )

    assert [assignment.axis for assignment in result.spec.axis_assignments] == [
        "left",
        "right",
    ]


def test_more_than_two_unit_groups_refuses_instead_of_guessing():
    frame = _frame(
        _column("loans", symbol="TRY"),
        _column("rate", symbol="percent", measure_type="rate"),
        _column("index", symbol="index", measure_type="index"),
    )

    with pytest.raises(ChartSelectionError) as error:
        select_chart(frame)

    assert error.value.reason == ChartSelectionRefusalReason.TOO_MANY_UNIT_GROUPS
    assert error.value.column_keys == ("loans", "rate", "index")


@pytest.mark.parametrize(
    ("column", "reason"),
    [
        (_column("missing-unit", unit_present=False), ChartSelectionRefusalReason.MISSING_UNIT),
        (
            _column("missing-symbol", symbol=None),
            ChartSelectionRefusalReason.MISSING_UNIT_SYMBOL,
        ),
        (
            _column("missing-measure", measure_type=None),
            ChartSelectionRefusalReason.MISSING_MEASURE_TYPE,
        ),
    ],
)
def test_ambiguous_metadata_produces_typed_refusal(column, reason):
    with pytest.raises(ChartSelectionError) as error:
        select_chart(_frame(column))

    assert error.value.reason == reason
    assert error.value.column_keys == (column.key,)


def test_magnitude_recommendation_ignores_null_zero_and_sign():
    result = select_chart(
        _frame(
            _column("large", values=(-1_000.0, None, 0.0, 1_200.0)),
            _column("small", values=(-1.0, 0.0, None, 1.0)),
        )
    )

    assert result.magnitude_threshold == DEFAULT_MAGNITUDE_THRESHOLD == 100.0
    assert result.magnitude_ratio == 1_100.0
    assert result.spec.indexing_recommended is True


def test_magnitude_recommendation_is_absent_without_two_nonzero_series():
    result = select_chart(
        _frame(
            _column("zero", values=(0.0, None, 0.0, None)),
            _column("small", values=(-1.0, 0.0, None, 1.0)),
        )
    )

    assert result.magnitude_ratio is None
    assert result.spec.indexing_recommended is False


@pytest.mark.parametrize(
    ("large_value", "expected"),
    [(99.0, False), (100.0, True)],
)
def test_fixed_magnitude_threshold_is_inclusive(large_value, expected):
    result = select_chart(
        _frame(
            _column("large", values=(large_value,) * 4),
            _column("small", values=(1.0,) * 4),
        )
    )

    assert result.spec.indexing_recommended is expected


def test_validated_override_changes_type_axes_and_title():
    frame = _frame(
        _column("loans"),
        _column("rate", symbol="percent", measure_type="rate"),
    )
    default = select_chart(frame)
    override = ChartSelectionOverride(
        chart_type="scatter",
        axis_assignments=(
            AxisAssignment(column_key="loans", axis="left"),
            AxisAssignment(column_key="rate", axis="left"),
        ),
        title="Controlled override",
    )

    result = select_chart(frame, override=override)

    assert result.spec.chart_type == "scatter"
    assert [item.axis for item in result.spec.axis_assignments] == ["left", "left"]
    assert result.spec.title == "Controlled override"
    assert result.spec.chart_id != default.spec.chart_id


@pytest.mark.parametrize(
    "assignments",
    [
        (AxisAssignment(column_key="unknown", axis="left"),),
        (
            AxisAssignment(column_key="rate", axis="left"),
            AxisAssignment(column_key="loans", axis="right"),
        ),
        (
            AxisAssignment(column_key="loans", axis="right"),
            AxisAssignment(column_key="rate", axis="right"),
        ),
    ],
)
def test_override_rejects_invalid_column_or_axis_references(assignments):
    frame = _frame(
        _column("loans"),
        _column("rate", symbol="percent", measure_type="rate"),
    )
    override = ChartSelectionOverride(axis_assignments=assignments)

    with pytest.raises(ChartSelectionError) as error:
        select_chart(frame, override=override)

    assert error.value.reason == ChartSelectionRefusalReason.INVALID_OVERRIDE


def test_override_contract_is_closed_and_requires_a_change():
    with pytest.raises(ValidationError):
        ChartSelectionOverride()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ChartSelectionOverride.model_validate({"chart_type": "line", "internal": True})


def test_explicit_non_numeric_or_unknown_columns_are_rejected():
    frame = _frame(_column("label", values=("a", "b", "c", "d"), dtype="string"))

    with pytest.raises(ChartSelectionError) as non_numeric:
        select_chart(frame, column_keys=("label",))
    assert non_numeric.value.reason == ChartSelectionRefusalReason.NON_NUMERIC_COLUMN

    with pytest.raises(ChartSelectionError) as unknown:
        select_chart(frame, column_keys=("missing",))
    assert unknown.value.reason == ChartSelectionRefusalReason.UNKNOWN_COLUMN
