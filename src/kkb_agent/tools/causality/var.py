"""VAR lag selection, fitting, directional tests, and diagnostics."""

from __future__ import annotations

import warnings
from math import isfinite

import numpy as np
from statsmodels.tsa.api import VAR

from .models import DiagnosticsResult, _RawDirectionalTest
from .stationarity import _require_finite_outputs


def _select_lag_bic(data: np.ndarray, max_lag: int, exog: np.ndarray | None) -> int | None:
    """Select lag order by BIC.  Returns None when no valid lag found."""
    if max_lag < 1:
        return None

    try:
        model = VAR(data, exog=exog)
    except Exception:
        return None
    best_lag: int | None = None
    best_bic = float("inf")

    for p in range(1, max_lag + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = model.fit(maxlags=p, trend="c")
            bic_val = float(result.bic)
            if bic_val < best_bic:
                best_bic = bic_val
                best_lag = p
        except Exception:
            continue
    return best_lag


# ---------------------------------------------------------------------------
# Internal: VAR Granger test
# ---------------------------------------------------------------------------


def _fit_var(
    data: np.ndarray,
    lag: int,
    exog: np.ndarray | None,
    sig: float,
) -> tuple[_RawDirectionalTest, _RawDirectionalTest, DiagnosticsResult] | None:
    try:
        model = VAR(data, exog=exog)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = model.fit(maxlags=lag, trend="c")
    except Exception:
        return None

    # Stability
    try:
        stable = bool(result.is_stable(verbose=False))
    except Exception:
        stable = False

    # Residual autocorrelation (Portmanteau / Ljung-Box)
    autocorr_pval: float | None = None
    try:
        wt = result.test_whiteness(nlags=max(5, 2 * lag), signif=sig, adjusted=False)
        candidate = float(wt.pvalue)
        autocorr_pval = candidate if isfinite(candidate) else None
    except Exception:
        pass

    n_eff = int(result.nobs)
    n_params = int(result.params.shape[0])
    dof = n_eff - n_params
    diag_passes = (
        (stable is not False) and dof > 0 and (autocorr_pval is None or autocorr_pval > sig)
    )
    diag_verified = (stable is True) and (autocorr_pval is not None) and dof > 0

    diagnostics = DiagnosticsResult(
        model_stable=stable,
        vecm_stability=None,
        residual_autocorrelation_p_value=autocorr_pval,
        degrees_of_freedom=max(dof, 0),
        parameter_count=n_params,
        passes=diag_passes,
        verified=diag_verified,
        explanation=_diagnostics_explanation(stable, autocorr_pval, dof, sig),
    )

    # Granger tests (Wald)
    try:
        gc_xy = result.test_causality(caused=1, causing=0, kind="wald")
        gc_yx = result.test_causality(caused=0, causing=1, kind="wald")
        _require_finite_outputs(
            gc_xy.test_statistic, gc_xy.pvalue, gc_yx.test_statistic, gc_yx.pvalue
        )
    except Exception:
        return None

    xy = _RawDirectionalTest(float(gc_xy.test_statistic), float(gc_xy.pvalue), "wald")
    yx = _RawDirectionalTest(float(gc_yx.test_statistic), float(gc_yx.pvalue), "wald")
    return xy, yx, diagnostics


def _diagnostics_explanation(
    stable: bool | None,
    autocorr_pval: float | None,
    dof: int,
    sig: float,
) -> str:
    parts: list[str] = []
    if stable is False:
        parts.append("VAR companion matrix has eigenvalues outside the unit circle (unstable)")
    elif stable is None:
        parts.append("model stability properties unverified")

    if autocorr_pval is not None and autocorr_pval <= sig:
        parts.append(f"Portmanteau test rejects white-noise residuals (p={autocorr_pval:.4f})")
    elif autocorr_pval is None:
        parts.append("residual whiteness unverified")

    if dof <= 0:
        parts.append(f"insufficient degrees of freedom ({dof})")

    if not parts:
        return (
            "All diagnostics verified and pass: model stable, residuals acceptable, positive d.o.f."
        )
    return ", ".join(parts)
