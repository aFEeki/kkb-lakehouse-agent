"""Deterministic anomaly detection for seasonal time series."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from numbers import Real
from typing import Literal

import numpy as np
from scipy.stats import median_abs_deviation
from statsmodels.tsa.seasonal import STL

DEFAULT_SENSITIVITY = 3.5
_MAD_NORMAL_SCALE = 1.4826


class AnomalyAnalysisError(ValueError):
    """Raised when anomaly analysis inputs cannot be analyzed safely."""


@dataclass(frozen=True)
class AnomalyParameters:
    period: int
    sensitivity: float
    minimum_segment_length: int
    decomposition: str = "STL(robust=True)"
    score_definition: str = "(residual - median(residual)) / (1.4826 * MAD)"


@dataclass(frozen=True)
class AnomalyPoint:
    index: int
    date: date | datetime
    observed_value: float
    expected_value: float
    residual: float
    robust_z_score: float
    direction: Literal["positive", "negative"]
    magnitude: float
    explanation: str


@dataclass(frozen=True)
class AnomalyResult:
    anomalies: tuple[AnomalyPoint, ...]
    residuals: tuple[float | None, ...]
    robust_z_scores: tuple[float | None, ...]
    parameters: AnomalyParameters
    observed_count: int
    missing_count: int


def analyze_anomalies(
    values: Sequence[Real | None],
    dates: Sequence[date | datetime],
    *,
    period: int,
    sensitivity: float = DEFAULT_SENSITIVITY,
) -> AnomalyResult:
    """Detect residual outliers after robust STL decomposition.

    Missing observations split the input into contiguous segments. They remain
    unscored and are never interpolated. At least one segment must contain two
    complete seasonal periods.
    """
    normalized_values, normalized_dates = _validate_inputs(values, dates, period, sensitivity)
    minimum_length = 2 * period
    residuals: list[float | None] = [None] * len(normalized_values)
    scores: list[float | None] = [None] * len(normalized_values)
    analyzed_segment_count = 0

    for start, stop in _observed_segments(normalized_values):
        if stop - start < minimum_length:
            continue
        segment = np.asarray(normalized_values[start:stop], dtype=float)
        result = STL(segment, period=period, robust=True).fit()
        segment_residuals = np.asarray(result.resid, dtype=float)
        center = float(np.median(segment_residuals))
        raw_mad = float(median_abs_deviation(segment_residuals, scale=1.0))
        scale = _robust_scale(raw_mad, segment_residuals, segment)
        for offset, residual in enumerate(result.resid):
            index = start + offset
            residuals[index] = float(residual)
            scores[index] = float((residual - center) / scale)
        analyzed_segment_count += 1

    if analyzed_segment_count == 0:
        raise AnomalyAnalysisError(
            f"At least one contiguous observed segment must contain {minimum_length} values "
            f"for period={period}"
        )

    anomalies = []
    for index, score in enumerate(scores):
        if score is None or abs(score) < sensitivity:
            continue
        observed = normalized_values[index]
        residual = residuals[index]
        assert observed is not None and residual is not None
        direction: Literal["positive", "negative"] = "positive" if score > 0 else "negative"
        magnitude = abs(score)
        anomalies.append(
            AnomalyPoint(
                index=index,
                date=normalized_dates[index],
                observed_value=observed,
                expected_value=observed - residual,
                residual=residual,
                robust_z_score=score,
                direction=direction,
                magnitude=magnitude,
                explanation=(
                    f"Residual is {magnitude:.2f} robust standard deviations "
                    f"{direction} relative to the local trend and seasonal regime."
                ),
            )
        )

    missing_count = sum(value is None for value in normalized_values)
    return AnomalyResult(
        anomalies=tuple(anomalies),
        residuals=tuple(residuals),
        robust_z_scores=tuple(scores),
        parameters=AnomalyParameters(
            period=period,
            sensitivity=float(sensitivity),
            minimum_segment_length=minimum_length,
        ),
        observed_count=len(normalized_values) - missing_count,
        missing_count=missing_count,
    )


def _validate_inputs(values, dates, period, sensitivity):
    if isinstance(period, bool) or not isinstance(period, int) or period < 2:
        raise AnomalyAnalysisError("period must be an integer greater than or equal to 2")
    if isinstance(sensitivity, bool) or not isinstance(sensitivity, Real):
        raise AnomalyAnalysisError("sensitivity must be a positive finite number")
    sensitivity = float(sensitivity)
    if not isfinite(sensitivity) or sensitivity <= 0:
        raise AnomalyAnalysisError("sensitivity must be a positive finite number")
    if len(values) != len(dates):
        raise AnomalyAnalysisError("values and dates must have the same length")

    normalized_dates = tuple(dates)
    if any(not isinstance(item, (date, datetime)) for item in normalized_dates):
        raise AnomalyAnalysisError("dates must contain only date or datetime values")
    try:
        strictly_increasing = all(
            current > previous
            for previous, current in zip(normalized_dates, normalized_dates[1:], strict=False)
        )
    except TypeError as error:
        raise AnomalyAnalysisError(
            "dates must be mutually comparable and strictly increasing"
        ) from error
    if not strictly_increasing:
        raise AnomalyAnalysisError("dates must be strictly increasing without duplicates")

    normalized_values: list[float | None] = []
    for value in values:
        if value is None or (isinstance(value, Real) and np.isnan(value)):
            normalized_values.append(None)
        elif isinstance(value, bool) or not isinstance(value, Real):
            raise AnomalyAnalysisError("values must contain only numeric or missing values")
        elif not isfinite(float(value)):
            raise AnomalyAnalysisError("infinite observations are invalid")
        else:
            normalized_values.append(float(value))
    return tuple(normalized_values), normalized_dates


def _observed_segments(values):
    start = None
    for index, value in enumerate((*values, None)):
        if value is not None and start is None:
            start = index
        elif value is None and start is not None:
            yield start, index
            start = None


def _robust_scale(raw_mad, residuals, values):
    scale = _MAD_NORMAL_SCALE * raw_mad
    observed = [value for value in values if value is not None]
    reference = max(1.0, float(np.max(np.abs(residuals))), max(abs(value) for value in observed))
    numerical_floor = np.finfo(float).eps * reference * 100
    return max(scale, numerical_floor)
