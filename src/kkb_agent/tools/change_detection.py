"""Deterministic level and trend change-point detection for time series."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite, log
from numbers import Real
from typing import Literal

import numpy as np
import ruptures as rpt

DEFAULT_MIN_SIZE = 6
DEFAULT_PENALTY_SCALE = 2.0
_COST_MODEL = "l2"
_MAD_NORMAL_SCALE = 1.4826


class ChangeDetectionError(ValueError):
    """Raised when change-point detection inputs cannot be analyzed safely."""


@dataclass(frozen=True)
class ChangePoint:
    index: int
    date: date | datetime
    kind: Literal["level", "trend"]
    before_value: float
    after_value: float
    magnitude: float
    explanation: str


@dataclass(frozen=True)
class ChangeDetectionParameters:
    method: str
    min_size: int
    penalty_scale: float
    minimum_segment_length: int
    penalty_formula: str = "penalty_scale * (1.4826 * MAD(segment_or_diff))**2 * log(n)"


@dataclass(frozen=True)
class ChangeDetectionResult:
    breakpoints: tuple[ChangePoint, ...]
    parameters: ChangeDetectionParameters
    observed_count: int
    missing_count: int


def detect_changes(
    values: Sequence[Real | None],
    dates: Sequence[date | datetime],
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    penalty_scale: float = DEFAULT_PENALTY_SCALE,
) -> ChangeDetectionResult:
    """Detect level and trend breakpoints with PELT, scored per contiguous segment.

    Missing observations split the input into contiguous segments, exactly as in the
    anomaly tool. A break is never inferred across a gap. Level breaks are found with
    PELT on the raw values; trend breaks are found independently with PELT on the
    first differences, so a slope change is detected even when it produces no jump in
    the series itself. At least one segment must contain two complete windows of
    `min_size` observations.
    """
    normalized_values, normalized_dates = _validate_inputs(values, dates, min_size, penalty_scale)
    minimum_length = 2 * min_size
    breakpoints: list[ChangePoint] = []
    analyzed_segment_count = 0

    for start, stop in _observed_segments(normalized_values):
        if stop - start < minimum_length:
            continue
        analyzed_segment_count += 1
        segment = normalized_values[start:stop]
        breakpoints.extend(
            _level_breakpoints(segment, normalized_dates, start, min_size, penalty_scale)
        )
        if stop - start >= minimum_length + 1:
            breakpoints.extend(
                _trend_breakpoints(segment, normalized_dates, start, min_size, penalty_scale)
            )

    if analyzed_segment_count == 0:
        raise ChangeDetectionError(
            f"At least one contiguous observed segment must contain {minimum_length} values "
            f"for min_size={min_size}"
        )

    breakpoints.sort(key=lambda point: (point.index, point.kind))
    missing_count = sum(value is None for value in normalized_values)
    return ChangeDetectionResult(
        breakpoints=tuple(breakpoints),
        parameters=ChangeDetectionParameters(
            method=f"PELT(model={_COST_MODEL!r})",
            min_size=min_size,
            penalty_scale=float(penalty_scale),
            minimum_segment_length=minimum_length,
        ),
        observed_count=len(normalized_values) - missing_count,
        missing_count=missing_count,
    )


def _level_breakpoints(segment, dates, start, min_size, penalty_scale) -> list[ChangePoint]:
    array = np.asarray(segment, dtype=float)
    penalty = _penalty(array, penalty_scale)
    local_bkps = _pelt_breakpoints(array, min_size, penalty)
    points = []
    boundaries = [0, *local_bkps, len(array)]
    for position, local_index in enumerate(local_bkps):
        before = float(np.mean(array[boundaries[position] : local_index]))
        after = float(np.mean(array[local_index : boundaries[position + 2]]))
        points.append(
            ChangePoint(
                index=start + local_index,
                date=dates[start + local_index],
                kind="level",
                before_value=before,
                after_value=after,
                magnitude=abs(after - before),
                explanation=(f"Mean level shifted from {before:.4g} to {after:.4g} at this point."),
            )
        )
    return points


def _trend_breakpoints(segment, dates, start, min_size, penalty_scale) -> list[ChangePoint]:
    array = np.asarray(segment, dtype=float)
    diffs = np.diff(array)
    penalty = _penalty(diffs, penalty_scale)
    local_bkps = _pelt_breakpoints(diffs, min_size, penalty)
    points = []
    boundaries = [0, *local_bkps, len(diffs)]
    for position, local_index in enumerate(local_bkps):
        before_slope = float(np.mean(diffs[boundaries[position] : local_index]))
        after_slope = float(np.mean(diffs[local_index : boundaries[position + 2]]))
        absolute_index = start + local_index + 1
        points.append(
            ChangePoint(
                index=absolute_index,
                date=dates[absolute_index],
                kind="trend",
                before_value=before_slope,
                after_value=after_slope,
                magnitude=abs(after_slope - before_slope),
                explanation=(
                    f"Per-period slope shifted from {before_slope:.4g} to "
                    f"{after_slope:.4g} at this point."
                ),
            )
        )
    return points


def _pelt_breakpoints(array: np.ndarray, min_size: int, penalty: float) -> list[int]:
    signal = array.reshape(-1, 1)
    algo = rpt.Pelt(model=_COST_MODEL, min_size=min_size, jump=1).fit(signal)
    all_breakpoints = algo.predict(pen=penalty)
    return all_breakpoints[:-1]  # ruptures always appends len(signal) as a closing marker


def _penalty(array: np.ndarray, penalty_scale: float) -> float:
    # A robust (MAD-based) scale, not the raw sample variance: a single level-jump
    # outlier in a diff array must not inflate the penalty enough to hide a genuine,
    # sustained trend change elsewhere in the same segment. Mirrors the anomaly tool's
    # own robust_z scale.
    median = float(np.median(array)) if len(array) else 0.0
    mad = float(np.median(np.abs(array - median))) if len(array) else 0.0
    robust_std = _MAD_NORMAL_SCALE * mad
    reference = max(1.0, float(np.max(np.abs(array))) if len(array) else 1.0)
    floor_std = float(np.sqrt(np.finfo(float).eps)) * reference
    scale = max(robust_std, floor_std)
    return penalty_scale * (scale**2) * log(max(len(array), 2))


def _validate_inputs(values, dates, min_size, penalty_scale):
    if isinstance(min_size, bool) or not isinstance(min_size, int) or min_size < 2:
        raise ChangeDetectionError("min_size must be an integer greater than or equal to 2")
    if isinstance(penalty_scale, bool) or not isinstance(penalty_scale, Real):
        raise ChangeDetectionError("penalty_scale must be a positive finite number")
    penalty_scale = float(penalty_scale)
    if not isfinite(penalty_scale) or penalty_scale <= 0:
        raise ChangeDetectionError("penalty_scale must be a positive finite number")
    if len(values) != len(dates):
        raise ChangeDetectionError("values and dates must have the same length")

    normalized_dates = tuple(dates)
    if any(not isinstance(item, (date, datetime)) for item in normalized_dates):
        raise ChangeDetectionError("dates must contain only date or datetime values")
    try:
        strictly_increasing = all(
            current > previous
            for previous, current in zip(normalized_dates, normalized_dates[1:], strict=False)
        )
    except TypeError as error:
        raise ChangeDetectionError(
            "dates must be mutually comparable and strictly increasing"
        ) from error
    if not strictly_increasing:
        raise ChangeDetectionError("dates must be strictly increasing without duplicates")

    normalized_values: list[float | None] = []
    for value in values:
        if value is None or (
            isinstance(value, Real) and not isinstance(value, bool) and np.isnan(value)
        ):
            normalized_values.append(None)
        elif isinstance(value, bool) or not isinstance(value, Real):
            raise ChangeDetectionError("values must contain only numeric or missing values")
        elif not isfinite(float(value)):
            raise ChangeDetectionError("infinite observations are invalid")
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
