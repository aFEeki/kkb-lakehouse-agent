"""VECM fitting, directional tests, and stability diagnostics."""

from __future__ import annotations

import warnings
from math import isfinite

import numpy as np
from statsmodels.tsa.vector_ar.vecm import VECM

from .models import DiagnosticsResult, VecmStabilityEvidence, _RawDirectionalTest
from .stationarity import _require_finite_outputs
from .var import _diagnostics_explanation

_UNIT_ROOT_TOLERANCE = 1e-4


def _fit_vecm(
    data: np.ndarray,
    lag: int,
    exog: np.ndarray | None,
    sig: float,
) -> tuple[_RawDirectionalTest, _RawDirectionalTest, DiagnosticsResult] | None:
    k_ar_diff = max(lag - 1, 0)
    try:
        model = VECM(data, k_ar_diff=k_ar_diff, coint_rank=1, deterministic="ci", exog=exog)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = model.fit()
    except Exception:
        return None

    # Diagnostics
    try:
        resid = np.asarray(result.resid, dtype=float)
        finite = bool(np.all(np.isfinite(resid)))
        n_eff = resid.shape[0]
        n_params = lag * 2 + 2  # approximate
        dof = n_eff - n_params
    except Exception:
        return None

    autocorr_pval: float | None = None
    try:
        # VECMResults in statsmodels does have test_whiteness
        wt = result.test_whiteness(nlags=max(5, 2 * lag), adjusted=False)
        candidate = float(wt.pvalue)
        autocorr_pval = candidate if isfinite(candidate) else None
    except Exception:
        pass

    diag_passes = finite and dof > 0 and (autocorr_pval is None or autocorr_pval > sig)

    # VECM Stability: For K variables and cointegration rank r, expect exactly K - r unit roots.
    # Other roots must be strictly inside the unit circle.
    model_stable: bool | None = None
    vecm_ev: VecmStabilityEvidence | None = None
    try:
        var_rep = result.var_rep
        p, k, _ = var_rep.shape
        companion = np.zeros((k * p, k * p))
        for i in range(p):
            companion[:k, i * k : (i + 1) * k] = var_rep[i]
        if p > 1:
            companion[k:, : k * (p - 1)] = np.eye(k * (p - 1))

        moduli = np.abs(np.linalg.eigvals(companion))
        expected_unit_roots = k - 1  # coint_rank=1
        unit_roots = np.sum(np.isclose(moduli, 1.0, atol=_UNIT_ROOT_TOLERANCE, rtol=0.0))
        non_unit_roots = [
            m for m in moduli if not np.isclose(m, 1.0, atol=_UNIT_ROOT_TOLERANCE, rtol=0.0)
        ]
        max_non_unit_root = max(non_unit_roots) if non_unit_roots else None

        if unit_roots == expected_unit_roots and (
            max_non_unit_root is None or max_non_unit_root < 1.0
        ):
            model_stable = True
        else:
            model_stable = False
            diag_passes = False

        vecm_ev = VecmStabilityEvidence(
            expected_unit_roots=expected_unit_roots,
            observed_unit_roots=int(unit_roots),
            tolerance=_UNIT_ROOT_TOLERANCE,
            largest_non_unit_root_modulus=max_non_unit_root,
            verified=bool(model_stable),
            explanation="Stability verified." if model_stable else "Failed eigenvalue constraints.",
        )
    except Exception:
        model_stable = None
        vecm_ev = VecmStabilityEvidence(
            expected_unit_roots=1,
            observed_unit_roots=None,
            tolerance=_UNIT_ROOT_TOLERANCE,
            largest_non_unit_root_modulus=None,
            verified=False,
            explanation="Companion-matrix eigenvalue verification could not be computed.",
        )

    diag_verified = (model_stable is True) and (autocorr_pval is not None) and dof > 0

    diagnostics = DiagnosticsResult(
        model_stable=model_stable,
        vecm_stability=vecm_ev,
        residual_autocorrelation_p_value=autocorr_pval,
        degrees_of_freedom=max(dof, 0),
        parameter_count=n_params,
        passes=diag_passes,
        verified=diag_verified,
        explanation=_diagnostics_explanation(model_stable, autocorr_pval, dof, sig),
    )

    # Granger tests
    try:
        gc_xy = result.test_granger_causality(caused=1, signif=sig)
        gc_yx = result.test_granger_causality(caused=0, signif=sig)
        _require_finite_outputs(
            gc_xy.test_statistic, gc_xy.pvalue, gc_yx.test_statistic, gc_yx.pvalue
        )
    except Exception:
        return None

    xy = _RawDirectionalTest(float(gc_xy.test_statistic), float(gc_xy.pvalue), "wald")
    yx = _RawDirectionalTest(float(gc_yx.test_statistic), float(gc_yx.pvalue), "wald")
    return xy, yx, diagnostics
