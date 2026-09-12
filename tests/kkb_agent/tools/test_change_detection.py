from datetime import date, timedelta

import pytest

from kkb_agent.tools import ChangeDetectionError, detect_changes


def _dates(length: int):
    start = date(2020, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(length))


def test_detects_a_clean_level_shift_at_the_exact_index():
    values = [100.0] * 30 + [160.0] * 30

    result = detect_changes(values, _dates(60), min_size=6)

    level_points = [point for point in result.breakpoints if point.kind == "level"]
    assert len(level_points) == 1
    assert level_points[0].index == 30
    assert level_points[0].before_value == pytest.approx(100.0)
    assert level_points[0].after_value == pytest.approx(160.0)
    assert level_points[0].magnitude == pytest.approx(60.0)
    assert level_points[0].date == _dates(60)[30]


def test_detects_a_trend_reversal_with_no_level_jump():
    # A clean "peak": slope +1 for 30 points, then slope -1 for 30 points, with no
    # repeated value at the joint (that would insert a spurious zero-diff plateau).
    up = list(range(30))  # 0 .. 29
    down = list(range(28, -2, -1))  # 28 .. -1, 30 points
    values = [float(v) for v in up + down]

    result = detect_changes(values, _dates(60), min_size=6)

    trend_points = [point for point in result.breakpoints if point.kind == "trend"]
    assert len(trend_points) == 1
    assert trend_points[0].index == 30
    assert trend_points[0].before_value == pytest.approx(1.0)
    assert trend_points[0].after_value == pytest.approx(-1.0)
    assert trend_points[0].magnitude == pytest.approx(2.0)


def test_detects_both_a_level_and_a_trend_break_in_one_series():
    # Flat at 50 for 24 points, jumps to 90 and ramps up by 2/period for 24 points,
    # then flattens again for 24 points: one level break, one (distinct) trend break.
    flat_low = [50.0] * 24
    ramp = [90.0 + 2.0 * i for i in range(24)]
    flat_high_start = ramp[-1]
    flat_high = [flat_high_start] * 24
    values = flat_low + ramp + flat_high

    result = detect_changes(values, _dates(len(values)), min_size=6)

    # The level jump and the trend change are genuinely distinct events at distinct
    # points: a level jump shows up as one outlier in the diff series, which min_size
    # prevents the trend pass from treating as its own regime.
    kinds_near_24 = {p.kind for p in result.breakpoints if abs(p.index - 24) <= 1}
    kinds_near_48 = {p.kind for p in result.breakpoints if abs(p.index - 48) <= 1}
    assert "level" in kinds_near_24
    assert "trend" in kinds_near_48


def test_missing_values_split_segments_and_breaks_never_cross_a_gap():
    first = [100.0] * 20 + [160.0] * 20
    second = [10.0] * 20 + [40.0] * 20
    values = first + [None] * 3 + second

    result = detect_changes(values, _dates(len(values)), min_size=6)

    # No breakpoint may reference an index inside the gap or bridge across it.
    gap_start, gap_stop = 40, 43
    assert all(not (gap_start <= point.index < gap_stop) for point in result.breakpoints)
    level_indices = sorted(p.index for p in result.breakpoints if p.kind == "level")
    assert 20 in level_indices
    assert 43 + 20 in level_indices


def test_constant_series_has_no_breakpoints():
    result = detect_changes([5.0] * 24, _dates(24), min_size=6)
    assert result.breakpoints == ()


def test_higher_penalty_scale_never_finds_more_breaks():
    values = [100.0] * 20 + [102.0] * 20 + [160.0] * 20  # a small wobble plus a real jump
    low = detect_changes(values, _dates(60), min_size=6, penalty_scale=1.0)
    high = detect_changes(values, _dates(60), min_size=6, penalty_scale=20.0)
    assert len(high.breakpoints) <= len(low.breakpoints)


def test_series_shorter_than_two_windows_is_rejected():
    with pytest.raises(ChangeDetectionError, match="12 values"):
        detect_changes([1.0] * 11, _dates(11), min_size=6)


@pytest.mark.parametrize(
    ("min_size", "penalty_scale"),
    [(1, 2.0), (2, 0), (2, float("inf")), (2, float("nan"))],
)
def test_invalid_parameters_are_rejected(min_size, penalty_scale):
    with pytest.raises(ChangeDetectionError):
        detect_changes([1.0] * 24, _dates(24), min_size=min_size, penalty_scale=penalty_scale)


def test_non_numeric_and_infinite_values_are_rejected():
    values = [1.0] * 24
    with pytest.raises(ChangeDetectionError, match="numeric or missing"):
        detect_changes([*values[:-1], "x"], _dates(24), min_size=6)
    with pytest.raises(ChangeDetectionError, match="infinite"):
        detect_changes([*values[:-1], float("inf")], _dates(24), min_size=6)


def test_bool_values_are_rejected_not_coerced():
    with pytest.raises(ChangeDetectionError, match="numeric or missing"):
        detect_changes([True] * 24, _dates(24), min_size=6)


@pytest.mark.parametrize(
    "dates",
    [
        (date(2020, 1, 1), date(2020, 1, 1)),
        (date(2020, 1, 2), date(2020, 1, 1)),
    ],
)
def test_dates_must_be_strictly_increasing(dates):
    with pytest.raises(ChangeDetectionError, match="strictly increasing"):
        detect_changes([1.0, 2.0], dates, min_size=2)


def test_results_are_deterministic_and_inputs_unchanged():
    values = [100.0] * 20 + [160.0] * 20
    original = list(values)

    first = detect_changes(values, _dates(40), min_size=6)
    second = detect_changes(values, _dates(40), min_size=6)

    assert first == second
    assert values == original


def test_parameters_are_reported_on_the_result():
    result = detect_changes([100.0] * 24, _dates(24), min_size=6, penalty_scale=3.0)
    assert result.parameters.min_size == 6
    assert result.parameters.penalty_scale == pytest.approx(3.0)
    assert result.parameters.minimum_segment_length == 12
    assert "PELT" in result.parameters.method
