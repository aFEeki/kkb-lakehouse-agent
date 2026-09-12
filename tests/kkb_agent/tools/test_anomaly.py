from datetime import date, timedelta

import numpy as np
import pytest

from kkb_agent.tools import AnomalyAnalysisError, analyze_anomalies


def _dates(length: int):
    start = date(2020, 1, 1)
    return tuple(start + timedelta(days=index) for index in range(length))


def _seasonal_values(length: int, period: int = 12):
    return [100 + 8 * np.sin(2 * np.pi * index / period) + 0.15 * index for index in range(length)]


def test_detects_isolated_positive_and_negative_spikes_with_magnitude():
    values = _seasonal_values(72)
    values[31] += 35
    values[50] -= 30

    result = analyze_anomalies(values, _dates(72), period=12)
    by_index = {point.index: point for point in result.anomalies}

    assert by_index[31].direction == "positive"
    assert by_index[50].direction == "negative"
    assert by_index[31].magnitude == abs(by_index[31].robust_z_score)
    assert "local trend and seasonal regime" in by_index[31].explanation


def test_recurring_seasonal_peaks_and_normal_points_are_not_flagged():
    result = analyze_anomalies(_seasonal_values(72), _dates(72), period=12)

    assert result.anomalies == ()


def test_sensitivity_changes_only_the_detection_threshold():
    values = [
        value + 0.5 * np.sin(index * 1.73) for index, value in enumerate(_seasonal_values(72))
    ]
    values[37] += 1
    low = analyze_anomalies(values, _dates(72), period=12, sensitivity=3.0)
    high = analyze_anomalies(values, _dates(72), period=12, sensitivity=6.0)

    assert {point.index for point in high.anomalies} < {point.index for point in low.anomalies}
    assert low.robust_z_scores == high.robust_z_scores


def test_missing_values_are_preserved_without_interpolation():
    values = _seasonal_values(61)
    values[30] = None
    values[45] += 30

    result = analyze_anomalies(values, _dates(61), period=12)

    assert result.residuals[30] is None
    assert result.robust_z_scores[30] is None
    assert all(point.index != 30 for point in result.anomalies)
    assert result.missing_count == 1


def test_each_observed_segment_uses_its_own_residual_distribution():
    first = [value + 0.2 * np.sin(index * 1.37) for index, value in enumerate(_seasonal_values(36))]
    second = [value + 4 * np.sin(index * 1.71) for index, value in enumerate(_seasonal_values(36))]
    combined = analyze_anomalies(first + [None] + second, _dates(73), period=12)
    first_result = analyze_anomalies(first, _dates(36), period=12)
    second_result = analyze_anomalies(second, _dates(36), period=12)

    assert combined.robust_z_scores[:36] == first_result.robust_z_scores
    assert combined.robust_z_scores[36] is None
    assert combined.robust_z_scores[37:] == second_result.robust_z_scores


def test_constant_series_is_safe_and_has_no_anomalies():
    result = analyze_anomalies([5.0] * 36, _dates(36), period=12)

    assert result.anomalies == ()
    assert all(score is not None and np.isfinite(score) for score in result.robust_z_scores)


@pytest.mark.parametrize(
    ("period", "sensitivity"),
    [(1, 3.5), (12, 0), (12, float("inf"))],
)
def test_invalid_parameters_are_rejected(period, sensitivity):
    with pytest.raises(AnomalyAnalysisError):
        analyze_anomalies([1.0] * 24, _dates(24), period=period, sensitivity=sensitivity)


def test_series_shorter_than_two_periods_is_rejected():
    with pytest.raises(AnomalyAnalysisError, match="24 values"):
        analyze_anomalies([1.0] * 23, _dates(23), period=12)


def test_nan_is_missing_but_infinity_is_rejected():
    values = _seasonal_values(48)
    values[24] = np.nan
    result = analyze_anomalies(values, _dates(48), period=12)
    assert result.robust_z_scores[24] is None

    values[24] = float("inf")
    with pytest.raises(AnomalyAnalysisError, match="infinite"):
        analyze_anomalies(values, _dates(48), period=12)


def test_results_are_deterministic_and_inputs_unchanged():
    values = _seasonal_values(48)
    original = list(values)

    first = analyze_anomalies(values, _dates(48), period=12)
    second = analyze_anomalies(values, _dates(48), period=12)

    assert first == second
    assert values == original


@pytest.mark.parametrize(
    "dates",
    [
        (date(2020, 1, 1), date(2020, 1, 1)),
        (date(2020, 1, 2), date(2020, 1, 1)),
    ],
)
def test_dates_must_be_strictly_increasing(dates):
    with pytest.raises(AnomalyAnalysisError, match="strictly increasing"):
        analyze_anomalies([1.0, 2.0], dates, period=2)
