from datetime import date, timedelta

import pytest

from kkb_agent.tools import ChangeDetectionError, detect_changes


def _dates(length: int):
    start = date(2020, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(length))


def test_detects_known_level_breakpoint():
    values = [0.0] * 60 + [12.0] * 60

    result = detect_changes(
        values,
        _dates(len(values)),
        level_penalty=100,
        trend_penalty=100,
        minimum_segment_length=10,
    )

    assert [(point.index, point.date) for point in result.for_kind("level")] == [
        (60, date(2020, 3, 1))
    ]


def test_trend_breakpoint_maps_first_difference_offset_to_original_date():
    values = [0.0]
    for _ in range(59):
        values.append(values[-1] + 1.0)
    for _ in range(60):
        values.append(values[-1] + 4.0)

    result = detect_changes(
        values,
        _dates(len(values)),
        level_penalty=10_000,
        trend_penalty=20,
        minimum_segment_length=10,
    )

    trend = result.for_kind("trend")
    assert [(point.index, point.date) for point in trend] == [(60, date(2020, 3, 1))]
    assert values[60] - values[59] == 4.0


def test_parameters_and_output_are_stable_for_downstream_consumers():
    values = [1.0] * 30 + [5.0] * 30
    kwargs = {
        "level_penalty": 20,
        "trend_penalty": 20,
        "minimum_segment_length": 8,
        "jump": 1,
    }

    first = detect_changes(values, _dates(len(values)), **kwargs)
    second = detect_changes(values, _dates(len(values)), **kwargs)

    assert first == second
    assert first.parameters.method == "PELT"
    assert first.parameters.model == "l2"
    assert first.parameters.level_penalty == 20
    assert first.parameters.trend_penalty == 20
    assert first.parameters.minimum_segment_length == 8
    assert first.parameters.jump == 1
    assert first.parameters.trend_transform == "first_difference"
    assert first.observation_count == 60


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"level_penalty": 0}, "level_penalty"),
        ({"trend_penalty": float("inf")}, "trend_penalty"),
        ({"minimum_segment_length": 1}, "minimum_segment_length"),
        ({"jump": 0}, "jump"),
    ],
)
def test_rejects_invalid_parameters(overrides, message):
    kwargs = {
        "level_penalty": 10,
        "trend_penalty": 10,
        "minimum_segment_length": 4,
        "jump": 1,
    }
    kwargs.update(overrides)

    with pytest.raises(ChangeDetectionError, match=message):
        detect_changes([1.0] * 20, _dates(20), **kwargs)


@pytest.mark.parametrize(
    ("values", "dates", "message"),
    [
        ([1.0] * 20, _dates(19), "same length"),
        ([1.0] * 8, _dates(8), "two complete segments"),
        ([1.0] * 19 + [None], _dates(20), "finite numeric"),
        ([1.0] * 19 + [float("nan")], _dates(20), "finite numeric"),
        ([1.0] * 20, (date(2020, 1, 1),) * 20, "strictly increasing"),
    ],
)
def test_rejects_inputs_that_cannot_be_analyzed(values, dates, message):
    with pytest.raises(ChangeDetectionError, match=message):
        detect_changes(
            values,
            dates,
            level_penalty=10,
            trend_penalty=10,
            minimum_segment_length=4,
        )
