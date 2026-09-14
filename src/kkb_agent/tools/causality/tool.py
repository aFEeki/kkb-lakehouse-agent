"""Public causality tool and internal decision-pipeline orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import numpy as np

from .breakpoints import (
    _build_break_dummies,
    _normalize_breakpoints,
    _regimes_too_small,
)
from .classification import _classify
from .cointegration import _test_cointegration
from .models import (
    BreakpointInput,
    CausalityError,
    CausalityResult,
    CausalMethod,
    CointegrationResult,
    DirectionalTestResult,
    RefusalReason,
    TodaYamamotoResult,
)
from .regimes import _apply_regime_robustness
from .robustness import _adjacent_lag_robustness, _holm_correct, _robustness_status
from .stationarity import _diagnose_stationarity
from .toda_yamamoto import _run_toda_yamamoto
from .validation import _drop_missing, _infer_frequency, _is_contiguous, _lag_cap, _validate_inputs
from .var import _fit_var, _select_lag_bic
from .vecm import _fit_vecm

_MIN_STATIONARITY_OBS = 50
_MIN_VAR_OBS = 80
_MIN_COINTEGRATION_OBS = 100
_MIN_VECM_OBS = 120
_MIN_TY_OBS = 120
_PARAM_RATIO = 10
_MIN_REGIME_OBS = 15


def _analyze_core_pipeline(
    x_clean: np.ndarray,
    y_clean: np.ndarray,
    dates_clean: Sequence[date],
    exog: np.ndarray | None,
    frequency: str,
    sig: float,
    max_lag: int | None,
    bp_inputs: tuple[BreakpointInput, ...],
    dropped: int,
) -> CausalityResult:
    n = len(x_clean)
    # --- Step 8: stationarity diagnosis -------------------------------------
    stat_x = _diagnose_stationarity(x_clean, dates_clean, sig)
    stat_y = _diagnose_stationarity(y_clean, dates_clean, sig)
    int_x = stat_x.conclusion
    int_y = stat_y.conclusion

    if int_x == "ambiguous" or int_y == "ambiguous":
        explanations = []
        if int_x == "ambiguous":
            explanations.append(f"X: {stat_x.explanation}")
        if int_y == "ambiguous":
            explanations.append(f"Y: {stat_y.explanation}")
        return _make_refusal(
            "stationarity_ambiguous",
            f"Integration order is ambiguous (X={int_x}, Y={int_y}). " + " ".join(explanations),
            n,
            dropped,
            dates_clean,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 9: branch selection -------------------------------------------

    # Compute true max integration order for metadata
    def _order(c):
        return 1 if c == "I(1)" else 0

    d_max = max(_order(int_x), _order(int_y))

    coint_result: CointegrationResult | None = None
    method: CausalMethod
    analysis_data: np.ndarray
    analysis_exog = exog
    cointegration_not_tested = False

    if int_x == "I(0)" and int_y == "I(0)":
        method = "granger_stationary_var"
        analysis_data = np.column_stack([x_clean, y_clean])

    elif int_x == "I(1)" and int_y == "I(1)":
        if n >= _MIN_COINTEGRATION_OBS:
            try:
                coint_result = _test_cointegration(x_clean, y_clean, sig)
            except Exception:
                return _make_refusal(
                    "cointegration_test_unavailable",
                    "Required Engle-Granger cointegration test could not be computed; "
                    "the model branch cannot be selected safely.",
                    n,
                    dropped,
                    dates_clean,
                    max_integration_order=d_max,
                    stationarity_x=stat_x,
                    stationarity_y=stat_y,
                    breakpoints_used=bp_inputs,
                    significance=sig,
                )
            if coint_result.cointegrated:
                if n >= _MIN_VECM_OBS:
                    method = "vecm"
                    analysis_data = np.column_stack([x_clean, y_clean])
                else:
                    return _make_refusal(
                        "cointegration_requires_unsupported_model",
                        f"Series are cointegrated I(1) but {n} observations is "
                        f"below the {_MIN_VECM_OBS} required for VECM.",
                        n,
                        dropped,
                        dates_clean,
                        max_integration_order=d_max,
                        stationarity_x=stat_x,
                        stationarity_y=stat_y,
                        cointegration=coint_result,
                        breakpoints_used=bp_inputs,
                        significance=sig,
                    )
            else:
                method = "granger_differenced_var"
                analysis_data = np.column_stack([np.diff(x_clean), np.diff(y_clean)])
                if analysis_exog is not None:
                    analysis_exog = analysis_exog[1:]
        else:
            method = "granger_differenced_var"
            analysis_data = np.column_stack([np.diff(x_clean), np.diff(y_clean)])
            if analysis_exog is not None:
                analysis_exog = analysis_exog[1:]
            cointegration_not_tested = True
    else:
        return _make_refusal(
            "mixed_integration_order",
            f"Mixed integration orders (X={int_x}, Y={int_y}). "
            f"No valid specification for this combination under the current policy.",
            n,
            dropped,
            dates_clean,
            max_integration_order=d_max,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 10: lag selection (BIC) ---------------------------------------
    lag_cap = _lag_cap(frequency)
    eff_max_lag = min(max_lag, lag_cap) if max_lag is not None else lag_cap
    eff_max_lag = max(1, min(eff_max_lag, len(analysis_data) // 4))  # hard safety
    selected_lag = _select_lag_bic(analysis_data, eff_max_lag, analysis_exog)

    if selected_lag is None or selected_lag < 1:
        return _make_refusal(
            "no_valid_lag",
            "BIC lag selection found no admissible lag order.",
            n,
            dropped,
            dates_clean,
            max_integration_order=d_max,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            cointegration=coint_result,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 11: parameter budget ------------------------------------------
    n_data = len(analysis_data)
    n_eff = n_data - selected_lag
    n_exog_cols = analysis_exog.shape[1] if analysis_exog is not None else 0
    q = 1 + 2 * selected_lag + n_exog_cols
    min_floor = _MIN_VECM_OBS if method == "vecm" else _MIN_VAR_OBS

    if n_eff < max(min_floor, _PARAM_RATIO * q):
        return _make_refusal(
            "insufficient_degrees_of_freedom",
            f"Effective sample {n_eff} is below max({min_floor}, "
            f"{_PARAM_RATIO}×{q}={_PARAM_RATIO * q}). "
            f"Selected lag={selected_lag}, exog={n_exog_cols}.",
            n,
            dropped,
            dates_clean,
            max_integration_order=d_max,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            cointegration=coint_result,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 12: fit model + diagnostics -----------------------------------
    fit_result = (
        _fit_vecm(analysis_data, selected_lag, analysis_exog, sig)
        if method == "vecm"
        else _fit_var(analysis_data, selected_lag, analysis_exog, sig)
    )
    if fit_result is None:
        return _make_refusal(
            "unstable_model",
            "Model estimation failed (singular design or degenerate parameters).",
            n,
            dropped,
            dates_clean,
            max_integration_order=d_max,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            cointegration=coint_result,
            breakpoints_used=bp_inputs,
            significance=sig,
        )
    xy_raw, yx_raw, diagnostics = fit_result

    if not diagnostics.passes:
        reason: RefusalReason = (
            "residual_diagnostics_failed"
            if diagnostics.residual_autocorrelation_p_value is not None
            and diagnostics.residual_autocorrelation_p_value <= sig
            else "unstable_model"
        )
        return _make_refusal(
            reason,
            f"Diagnostics failed: {diagnostics.explanation}",
            n,
            dropped,
            dates_clean,
            max_integration_order=d_max,
            stationarity_x=stat_x,
            stationarity_y=stat_y,
            cointegration=coint_result,
            breakpoints_used=bp_inputs,
            diagnostics=diagnostics,
            significance=sig,
        )

    # --- Step 13: Holm correction -------------------------------------------
    corrected = _holm_correct([xy_raw.p_value, yx_raw.p_value])

    x_to_y = DirectionalTestResult(
        direction="x_to_y",
        statistic=xy_raw.statistic,
        p_value=xy_raw.p_value,
        corrected_p_value=corrected[0],
        significant=corrected[0] < sig,
        lags_tested=selected_lag,
        test_type=xy_raw.test_type,
    )
    y_to_x = DirectionalTestResult(
        direction="y_to_x",
        statistic=yx_raw.statistic,
        p_value=yx_raw.p_value,
        corrected_p_value=corrected[1],
        significant=corrected[1] < sig,
        lags_tested=selected_lag,
        test_type=yx_raw.test_type,
    )

    # --- Step 14: adjacent-lag robustness -----------------------------------
    adjacent_evaluation = _adjacent_lag_robustness(
        analysis_data,
        selected_lag,
        eff_max_lag,
        analysis_exog,
        method,
        sig,
    )
    robustness_status = _robustness_status(x_to_y, y_to_x, adjacent_evaluation)
    robustness = adjacent_evaluation.results

    # --- Step 15: Toda-Yamamoto robustness (if eligible) --------------------
    ty_result: TodaYamamotoResult | None = None
    if d_max > 0 and n >= _MIN_TY_OBS:
        levels_data = np.column_stack([x_clean, y_clean])
        p_ty = _select_lag_bic(levels_data, eff_max_lag, exog)
        if p_ty is not None and p_ty >= 1:
            ty_n_eff = n - (p_ty + d_max)
            q_aug = 1 + 2 * (p_ty + d_max) + n_exog_cols
            if ty_n_eff >= max(_MIN_TY_OBS, _PARAM_RATIO * q_aug):
                try:
                    ty_result = _run_toda_yamamoto(
                        levels_data,
                        p_ty,
                        d_max,
                        exog,
                    )
                except Exception:
                    ty_result = None

    # --- Step 16: classify result -------------------------------------------
    diag_unverified = not diagnostics.verified
    sensitivity_unverified = stat_x.za_unverified or stat_y.za_unverified
    status, refusal_reason, explanation = _classify(
        x_to_y,
        y_to_x,
        robustness_status,
        cointegration_not_tested,
        diag_unverified,
        sensitivity_unverified,
    )

    return CausalityResult(
        status=status,
        method=method,
        effective_observations=n,
        effective_date_range=(dates_clean[0], dates_clean[-1]),
        dropped_observations=dropped,
        selected_lag=selected_lag,
        max_integration_order=d_max,
        stationarity_x=stat_x,
        stationarity_y=stat_y,
        cointegration=coint_result,
        breakpoints_used=bp_inputs,
        x_to_y=x_to_y,
        y_to_x=y_to_x,
        robustness=robustness,
        toda_yamamoto=ty_result,
        regimes=None,
        diagnostics=diagnostics,
        significance_threshold=sig,
        refusal_reason=refusal_reason,
        refusal_details=explanation if status == "not_identifiable" else None,
        explanation=explanation,
    )


def analyze_causality(
    x_values: Sequence[float | None],
    y_values: Sequence[float | None],
    dates: Sequence[date | datetime],
    *,
    max_lag: int | None = None,
    breakpoints: Sequence[BreakpointInput] = (),
    significance: float = 0.05,
) -> CausalityResult:
    """Deterministic Granger-predictive causality analysis.

    Operates on already-supplied numeric series and dates.  Data acquisition
    is the Lakehouse / agent layer's responsibility.

    Parameters
    ----------
    x_values, y_values : sequences of float or None
        Numeric observations.  ``None`` marks missing values which are dropped
        (never interpolated).
    dates : sequence of ``date`` or ``datetime``
        Strictly increasing unique observation dates.
    max_lag : int or None
        Explicit maximum lag; capped by frequency-specific guardrails.
    breakpoints : sequence of ``BreakpointInput``
        Structural-break dates from the change-detection tool.
    significance : float
        Family-wise significance level (default 0.05).

    Returns
    -------
    CausalityResult
        Typed, immutable, serializable result.  ``status`` is one of
        ``predictive_relationship_supported``, ``limited_evidence``, or
        ``not_identifiable``.
    """
    # --- Step 1: validate & normalise inputs --------------------------------
    x_arr, y_arr, dates_arr = _validate_inputs(x_values, y_values, dates, significance, max_lag)
    try:
        sig = float(significance)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CausalityError("significance must be a positive finite number") from exc

    # --- Step 2: infer frequency from original dates ------------------------
    frequency = _infer_frequency(dates_arr)
    if frequency == "unknown":
        return _make_refusal(
            "irregular_frequency",
            "Could not detect a regular monthly, weekly, or daily frequency from the dates. "
            "A valid lag interpretation cannot be established for irregular data.",
            len(x_arr),
            0,
            dates_arr,
            significance=sig,
        )

    # --- Step 3: drop missing values ----------------------------------------
    x_clean, y_clean, dates_clean, dropped = _drop_missing(x_arr, y_arr, dates_arr)
    n = len(x_clean)
    bp_inputs = _normalize_breakpoints(breakpoints, dates_clean)

    if n < 2:
        return _make_refusal(
            "insufficient_observations",
            f"Only {n} complete observations after removing {dropped} missing rows.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 4: contiguity after missing removal ---------------------------
    if not _is_contiguous(dates_clean, frequency):
        return _make_refusal(
            "non_contiguous_after_missing_removal",
            f"Removing {dropped} missing observations created non-contiguous gaps "
            f"in the {frequency} series. The remaining {n} observations cannot form "
            f"a valid regular time series for VAR modelling.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 5: sample size gate -------------------------------------------
    if n < _MIN_STATIONARITY_OBS:
        return _make_refusal(
            "insufficient_observations",
            f"{n} observations is below the minimum {_MIN_STATIONARITY_OBS} "
            f"required for ADF/KPSS stationarity diagnostics.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 6: constant-series check --------------------------------------
    if np.ptp(x_clean) == 0:
        return _make_refusal(
            "constant_series",
            "X series has zero variance.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )
    if np.ptp(y_clean) == 0:
        return _make_refusal(
            "constant_series",
            "Y series has zero variance.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # --- Step 7: breakpoint validation & dummies ----------------------------
    exog = _build_break_dummies(bp_inputs, dates_clean)
    if bp_inputs and _regimes_too_small(bp_inputs, dates_clean, _MIN_REGIME_OBS):
        return _make_refusal(
            "structural_break_confounding",
            f"Supplied breakpoints fragment the {n} observations into regimes "
            f"smaller than the minimum {_MIN_REGIME_OBS}.",
            n,
            dropped,
            dates_clean,
            breakpoints_used=bp_inputs,
            significance=sig,
        )

    # Run global core pipeline
    global_result = _analyze_core_pipeline(
        x_clean, y_clean, dates_clean, exog, frequency, sig, max_lag, bp_inputs, dropped
    )

    # If not positive, or no breakpoints, just return global
    if global_result.status != "predictive_relationship_supported" or not bp_inputs:
        return global_result

    return _apply_regime_robustness(
        global_result,
        x_clean,
        y_clean,
        dates_clean,
        bp_inputs,
        frequency,
        sig,
        max_lag,
        _analyze_core_pipeline,
        _MIN_REGIME_OBS,
        _MIN_STATIONARITY_OBS,
    )


def _make_refusal(
    reason: RefusalReason, details: str, n: int, dropped: int, dates: tuple, **kwargs
) -> CausalityResult:
    stationarity_x = kwargs.get("stationarity_x")
    stationarity_y = kwargs.get("stationarity_y")
    cointegration = kwargs.get("cointegration")
    breakpoints_used = kwargs.get("breakpoints_used", ())
    diagnostics = kwargs.get("diagnostics")
    significance = kwargs.get("significance", 0.05)

    return CausalityResult(
        status="not_identifiable",
        method=None,
        effective_observations=n,
        effective_date_range=(dates[0], dates[-1]) if len(dates) >= 2 else None,
        dropped_observations=dropped,
        selected_lag=None,
        max_integration_order=kwargs.get("max_integration_order", 0),
        stationarity_x=stationarity_x,
        stationarity_y=stationarity_y,
        cointegration=cointegration,
        breakpoints_used=breakpoints_used,
        x_to_y=None,
        y_to_x=None,
        robustness=None,
        toda_yamamoto=None,
        regimes=None,
        diagnostics=diagnostics,
        significance_threshold=significance,
        refusal_reason=reason,
        refusal_details=details,
        explanation=f"Causality is not identifiable: {details}",
    )
