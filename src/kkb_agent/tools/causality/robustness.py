"""Multiplicity correction and adjacent-lag robustness bookkeeping."""

from __future__ import annotations

from typing import Literal

import numpy as np

from .models import (
    CausalMethod,
    DirectionalTestResult,
    RobustnessResult,
    _AdjacentLagEvaluation,
)
from .var import _fit_var
from .vecm import _fit_vecm

_MIN_VAR_OBS = 80
_MIN_VECM_OBS = 120
_PARAM_RATIO = 10


def _holm_correct(p_values: list[float]) -> list[float]:
    """Holm step-down procedure.  Returns Holm-adjusted p-values."""
    m = len(p_values)
    ordered = sorted(range(m), key=lambda i: p_values[i])
    corrected = [0.0] * m
    running_max = 0.0
    for rank, orig_idx in enumerate(ordered):
        adjusted = (m - rank) * p_values[orig_idx]
        running_max = max(running_max, adjusted)
        corrected[orig_idx] = min(1.0, running_max)
    return corrected


# ---------------------------------------------------------------------------
# Internal: adjacent-lag robustness
# ---------------------------------------------------------------------------


def _adjacent_lag_robustness(
    data: np.ndarray,
    selected_lag: int,
    max_lag: int,
    exog: np.ndarray | None,
    method: CausalMethod,
    sig: float,
) -> _AdjacentLagEvaluation:
    results: list[RobustnessResult] = []
    applicable: list[int] = []
    unavailable: list[int] = []
    for alt_lag in [selected_lag - 1, selected_lag + 1]:
        if alt_lag < 1 or alt_lag > max_lag:
            continue
        n_eff = len(data) - alt_lag
        n_exog_cols = exog.shape[1] if exog is not None else 0
        q = 1 + 2 * alt_lag + n_exog_cols
        floor = _MIN_VECM_OBS if method == "vecm" else _MIN_VAR_OBS
        applicable.append(alt_lag)
        if n_eff < max(floor, _PARAM_RATIO * q):
            unavailable.append(alt_lag)
            continue
        try:
            fit_fn = _fit_vecm if method == "vecm" else _fit_var
            fit_result = fit_fn(data, alt_lag, exog, sig)
            if fit_result is None:
                unavailable.append(alt_lag)
                continue
            xy_raw, yx_raw, diag = fit_result
            if not diag.passes or not diag.verified:
                unavailable.append(alt_lag)
                continue
            corrected = _holm_correct([xy_raw.p_value, yx_raw.p_value])
            results.append(
                RobustnessResult(
                    lag_order=alt_lag,
                    x_to_y_p_value=corrected[0],
                    y_to_x_p_value=corrected[1],
                    x_to_y_significant=corrected[0] < sig,
                    y_to_x_significant=corrected[1] < sig,
                )
            )
        except Exception:
            unavailable.append(alt_lag)
            continue
    return _AdjacentLagEvaluation(
        results=tuple(results),
        applicable_lags=tuple(applicable),
        unavailable_lags=tuple(unavailable),
    )


def _direction_state(xy_significant: bool, yx_significant: bool) -> str:
    if xy_significant and yx_significant:
        return "bidirectional"
    if xy_significant:
        return "x_to_y"
    if yx_significant:
        return "y_to_x"
    return "none"


def _robustness_status(
    xy: DirectionalTestResult,
    yx: DirectionalTestResult,
    evaluation: _AdjacentLagEvaluation,
) -> Literal["passed", "reversed", "inconclusive", "unavailable"]:
    """Classify all applicable adjacent lags without hiding unavailable checks."""
    if not evaluation.applicable_lags:
        return "unavailable"

    selected_direction = _direction_state(xy.significant, yx.significant)
    inconclusive = False
    for result in evaluation.results:
        adjacent_direction = _direction_state(result.x_to_y_significant, result.y_to_x_significant)
        if (selected_direction, adjacent_direction) in {
            ("x_to_y", "y_to_x"),
            ("y_to_x", "x_to_y"),
        }:
            return "reversed"
        if selected_direction != adjacent_direction:
            inconclusive = True

    if evaluation.unavailable_lags:
        return "unavailable"
    if inconclusive:
        return "inconclusive"
    return "passed"
