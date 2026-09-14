"""Engle-Granger cointegration testing."""

from __future__ import annotations

import warnings

import numpy as np
from statsmodels.tsa.stattools import coint

from .models import CointegrationResult
from .stationarity import _require_finite_outputs


def _test_cointegration(x: np.ndarray, y: np.ndarray, sig: float) -> CointegrationResult:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        coint_t, pvalue, cvs = coint(x, y, trend="c", autolag="AIC")
    _require_finite_outputs(coint_t, pvalue, *cvs)
    cvs_dict = {"1%": float(cvs[0]), "5%": float(cvs[1]), "10%": float(cvs[2])}
    cointegrated = float(pvalue) < sig
    return CointegrationResult(
        method="engle_granger",
        statistic=float(coint_t),
        p_value=float(pvalue),
        critical_values=cvs_dict,
        cointegrated=cointegrated,
        explanation=(
            "Engle-Granger test rejects no-cointegration → cointegrated."
            if cointegrated
            else "Engle-Granger test cannot reject no-cointegration → not cointegrated."
        ),
    )
