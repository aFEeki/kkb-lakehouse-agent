"""Input normalization and time-axis validation."""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from numbers import Real

import numpy as np

from .models import CausalityError

_MIN_VECM_OBS = 120
_MIN_VAR_OBS = 80
_MONTHLY_LAG_CAP = 3
_WEEKLY_LAG_CAP = 8
_DAILY_LAG_CAP = 10


def _validate_inputs(x_values, y_values, dates, significance, max_lag):
    if len(x_values) != len(y_values):
        raise CausalityError("x_values and y_values must have the same length")
    if len(x_values) != len(dates):
        raise CausalityError("x_values, y_values, and dates must have the same length")
    if len(x_values) == 0:
        raise CausalityError("input sequences must not be empty")

    # significance
    if isinstance(significance, bool) or not isinstance(significance, Real):
        raise CausalityError("significance must be a positive finite number")
    try:
        normalized_significance = float(significance)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CausalityError("significance must be a positive finite number") from exc
    if (
        not isfinite(normalized_significance)
        or normalized_significance <= 0
        or normalized_significance >= 1
    ):
        raise CausalityError("significance must be between 0 and 1 (exclusive)")

    # max_lag
    if max_lag is not None:
        if isinstance(max_lag, bool) or not isinstance(max_lag, int) or max_lag < 1:
            raise CausalityError("max_lag must be a positive integer or None")

    # dates
    dates_arr = tuple(dates)
    if any(not isinstance(d, (date, datetime)) for d in dates_arr):
        raise CausalityError("dates must contain only date or datetime values")
    try:
        strictly_increasing = all(
            curr > prev for prev, curr in zip(dates_arr, dates_arr[1:], strict=False)
        )
    except TypeError as exc:
        raise CausalityError("dates must be mutually comparable and strictly increasing") from exc
    if not strictly_increasing:
        raise CausalityError("dates must be strictly increasing without duplicates")

    # values — accept None for missing, Real for observed, reject bool/inf
    x_arr: list[float | None] = []
    y_arr: list[float | None] = []
    for label, src, dest in [("x", x_values, x_arr), ("y", y_values, y_arr)]:
        for value in src:
            if value is None:
                dest.append(None)
                continue
            if isinstance(value, bool) or not isinstance(value, Real):
                raise CausalityError(f"{label}_values must contain only numeric or missing values")
            try:
                normalized_value = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CausalityError(
                    f"{label}_values contain a numeric value that cannot be converted to float"
                ) from exc
            if np.isnan(normalized_value):
                dest.append(None)
            elif not isfinite(normalized_value):
                raise CausalityError(f"infinite observations in {label}_values are invalid")
            else:
                dest.append(normalized_value)

    return tuple(x_arr), tuple(y_arr), dates_arr


# ---------------------------------------------------------------------------
# Internal: missing-value handling
# ---------------------------------------------------------------------------


def _drop_missing(x_arr, y_arr, dates_arr):
    """Drop rows where either series is missing.  Never interpolate."""
    mask = [i for i in range(len(x_arr)) if x_arr[i] is not None and y_arr[i] is not None]
    x_clean = np.array([x_arr[i] for i in mask], dtype=float)
    y_clean = np.array([y_arr[i] for i in mask], dtype=float)
    dates_clean = tuple(dates_arr[i] for i in mask)
    dropped = len(x_arr) - len(mask)
    return x_clean, y_clean, dates_clean, dropped


# ---------------------------------------------------------------------------
# Internal: frequency detection & contiguity
# ---------------------------------------------------------------------------


def _infer_frequency(dates_arr) -> str:
    if len(dates_arr) < 2:
        return "unknown"
    diffs = [(dates_arr[i + 1] - dates_arr[i]).days for i in range(len(dates_arr) - 1)]
    median_diff = float(sorted(diffs)[len(diffs) // 2])
    if 25 <= median_diff <= 35:
        return "monthly"
    if 6 <= median_diff <= 8:
        return "weekly"
    if 1 <= median_diff <= 5:
        return "daily"
    return "unknown"


def _is_contiguous(dates_clean, frequency) -> bool:
    """Verify remaining dates are still regularly spaced at the detected frequency."""
    if len(dates_clean) < 2:
        return True
    if frequency == "monthly":
        for i in range(1, len(dates_clean)):
            d0, d1 = dates_clean[i - 1], dates_clean[i]
            month_diff = (d1.year - d0.year) * 12 + (d1.month - d0.month)
            if month_diff != 1:
                return False
    elif frequency == "weekly":
        for i in range(1, len(dates_clean)):
            if (dates_clean[i] - dates_clean[i - 1]).days != 7:
                return False
    elif frequency == "daily":
        for i in range(1, len(dates_clean)):
            diff = (dates_clean[i] - dates_clean[i - 1]).days
            if diff < 1 or diff > 3:
                return False
    # "unknown" — skip contiguity check; downstream gates still apply
    return True


def _lag_cap(frequency) -> int:
    caps = {"monthly": _MONTHLY_LAG_CAP, "weekly": _WEEKLY_LAG_CAP, "daily": _DAILY_LAG_CAP}
    return caps.get(frequency, _MONTHLY_LAG_CAP)
