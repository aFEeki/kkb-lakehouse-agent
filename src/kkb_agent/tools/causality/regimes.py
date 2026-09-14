"""Independent regime-split robustness evaluation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime
from typing import Literal

import numpy as np

from .breakpoints import _calendar_date
from .models import BreakpointInput, CausalityResult, RegimeResult


def _apply_regime_robustness(
    global_result: CausalityResult,
    x_clean: np.ndarray,
    y_clean: np.ndarray,
    dates_clean: Sequence[date | datetime],
    breakpoints: tuple[BreakpointInput, ...],
    frequency: str,
    significance: float,
    max_lag: int | None,
    core_pipeline: Callable[..., CausalityResult],
    min_regime_observations: int,
    min_stationarity_observations: int,
) -> CausalityResult:
    """Run the existing core pipeline independently within every break regime."""
    boundaries = [0]
    for breakpoint in breakpoints:
        index = next(
            (
                i
                for i, observation_date in enumerate(dates_clean)
                if _calendar_date(observation_date) >= _calendar_date(breakpoint.date)
            ),
            None,
        )
        if index is not None:
            boundaries.append(index)
    boundaries.append(len(x_clean))
    boundaries = sorted(set(boundaries))

    regime_results: list[RegimeResult] = []
    regime_pass_count = 0
    regime_reversal = False

    for regime_index in range(len(boundaries) - 1):
        start = boundaries[regime_index]
        end = boundaries[regime_index + 1]
        regime_x = x_clean[start:end]
        regime_y = y_clean[start:end]
        regime_dates = dates_clean[start:end]
        observation_count = len(regime_x)

        if (
            observation_count < min_regime_observations
            or observation_count < min_stationarity_observations
        ):
            regime_results.append(
                RegimeResult(
                    regime_index=regime_index + 1,
                    date_range=(regime_dates[0], regime_dates[-1])
                    if observation_count > 0
                    else (dates_clean[0], dates_clean[-1]),
                    effective_observations=observation_count,
                    status="unavailable",
                    method=None,
                    selected_lag=None,
                    x_to_y=None,
                    y_to_x=None,
                    diagnostics=None,
                    reason="Insufficient sample size for regime estimation",
                )
            )
            continue

        regime_result = core_pipeline(
            regime_x,
            regime_y,
            regime_dates,
            None,
            frequency,
            significance,
            max_lag,
            (),
            0,
        )
        status: Literal["passed", "reversed", "inconclusive", "unavailable"]

        if regime_result.x_to_y is None or regime_result.y_to_x is None:
            status = "unavailable"
        else:
            global_direction = _direction(
                global_result.x_to_y.significant if global_result.x_to_y else False,
                global_result.y_to_x.significant if global_result.y_to_x else False,
            )
            regime_direction = _direction(
                regime_result.x_to_y.significant,
                regime_result.y_to_x.significant,
            )
            if global_direction == regime_direction:
                if regime_result.status != "predictive_relationship_supported":
                    status = "inconclusive"
                else:
                    status = "passed"
                    regime_pass_count += 1
            elif (global_direction, regime_direction) in {
                ("x_to_y", "y_to_x"),
                ("y_to_x", "x_to_y"),
            }:
                status = "reversed"
                regime_reversal = True
            else:
                status = "inconclusive"

        regime_results.append(
            RegimeResult(
                regime_index=regime_index + 1,
                date_range=(regime_dates[0], regime_dates[-1]),
                effective_observations=observation_count,
                status=status,
                method=regime_result.method,
                selected_lag=regime_result.selected_lag,
                x_to_y=regime_result.x_to_y,
                y_to_x=regime_result.y_to_x,
                diagnostics=regime_result.diagnostics,
                reason=regime_result.refusal_details or regime_result.explanation,
            )
        )

    if regime_reversal:
        return replace(
            global_result,
            regimes=tuple(regime_results),
            status="not_identifiable",
            refusal_reason="regime_robustness_failed",
            refusal_details=(
                "Regime estimation yielded a significant reversal of the global direction."
            ),
            explanation=(
                "Global causality unsupported: a validated regime reversed the "
                "directional conclusion."
            ),
        )
    if regime_pass_count < len(regime_results):
        return replace(
            global_result,
            regimes=tuple(regime_results),
            status="limited_evidence",
            explanation=global_result.explanation
            + " Capped at limited_evidence: required regime robustness unavailable "
            "or inconclusive.",
        )
    return replace(global_result, regimes=tuple(regime_results))


def _direction(x_to_y: bool, y_to_x: bool) -> str:
    if x_to_y and y_to_x:
        return "bidirectional"
    if x_to_y:
        return "x_to_y"
    if y_to_x:
        return "y_to_x"
    return "none"
