"""Stationarity diagnostics and structural-break sensitivity."""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from datetime import date, datetime
from math import isfinite

import numpy as np
from statsmodels.tsa.stattools import adfuller, kpss, zivot_andrews

from .models import StationarityDiagnosis, StationarityTestResult, ZivotAndrewsResult

_STATIONARITY_REGRESSION = "c"


def _require_finite_outputs(*values) -> None:
    if any(not isfinite(float(value)) for value in values):
        raise ValueError("Statistical test returned a non-finite result")


def _run_adf(series: np.ndarray, regression: str, sig: float) -> StationarityTestResult:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, pvalue, usedlag, _, cvs, _ = adfuller(series, regression=regression, autolag="AIC")
    _require_finite_outputs(stat, pvalue, *cvs.values())
    return StationarityTestResult(
        test_name="ADF",
        statistic=float(stat),
        p_value=float(pvalue),
        critical_values={k: float(v) for k, v in cvs.items()},
        regression=regression,
        lags_or_bandwidth=int(usedlag),
        interpretation=(
            "Reject unit root (stationary evidence)" if pvalue < sig else "Cannot reject unit root"
        ),
    )


def _run_kpss(series: np.ndarray, regression: str, sig: float) -> StationarityTestResult:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, pvalue, lags, cvs = kpss(series, regression=regression)
    _require_finite_outputs(stat, pvalue, *cvs.values())
    return StationarityTestResult(
        test_name="KPSS",
        statistic=float(stat),
        p_value=float(pvalue),
        critical_values={k: float(v) for k, v in cvs.items()},
        regression=regression,
        lags_or_bandwidth=int(lags),
        interpretation=(
            "Reject stationarity (non-stationary evidence)"
            if pvalue < sig
            else "Cannot reject stationarity"
        ),
    )


def _run_zivot_andrews(
    series: np.ndarray, dates: Sequence[date | datetime], regression: str, sig: float
) -> ZivotAndrewsResult:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, pvalue, cvs, baselag, bpidx = zivot_andrews(
            series, regression=regression, autolag="AIC"
        )
    _require_finite_outputs(stat, pvalue, *cvs.values())
    return ZivotAndrewsResult(
        statistic=float(stat),
        p_value=float(pvalue),
        critical_values={str(k): float(v) for k, v in cvs.items()},
        estimated_break_date=dates[int(bpidx)],
        regression=regression,
        lags_used=int(baselag),
        interpretation=(
            "Reject unit root (stationary with break evidence)"
            if pvalue < sig
            else "Cannot reject unit root"
        ),
    )


def _diagnose_stationarity(
    series: np.ndarray, dates: Sequence[date | datetime], sig: float
) -> StationarityDiagnosis:
    """Paired ADF/KPSS with optional first-difference follow-up and ZA sensitivity."""
    reg = _STATIONARITY_REGRESSION
    adf_level = None
    kpss_level = None
    try:
        adf_level = _run_adf(series, reg, sig)
    except Exception:
        pass
    try:
        kpss_level = _run_kpss(series, reg, sig)
    except Exception:
        pass
    if adf_level is None or kpss_level is None:
        return StationarityDiagnosis(
            adf_level=adf_level,
            kpss_level=kpss_level,
            adf_first_diff=None,
            kpss_first_diff=None,
            zivot_andrews=None,
            conclusion="ambiguous",
            explanation=(
                "Stationarity test failed to compute: required ADF/KPSS results unavailable."
            ),
        )

    za_result = None
    za_unverified = False
    if len(series) >= 80:
        try:
            za_result = _run_zivot_andrews(series, dates, reg, sig)
        except Exception:
            za_unverified = True

    adf_rejects = adf_level.p_value is not None and adf_level.p_value < sig
    kpss_rejects = kpss_level.p_value is not None and kpss_level.p_value < sig

    adf_diff: StationarityTestResult | None = None
    kpss_diff: StationarityTestResult | None = None

    if adf_rejects and not kpss_rejects:
        # ADF/KPSS say I(0) (stationary).
        # ZA tests null of unit root. If ZA fails to reject, it says unit root.
        # The conservative rule specifies ambiguity only when ZA rejects while
        # ADF/KPSS indicates I(1).
        # Here we do not override to ambiguous just because ZA fails to reject.

        return StationarityDiagnosis(
            adf_level=adf_level,
            kpss_level=kpss_level,
            adf_first_diff=None,
            kpss_first_diff=None,
            zivot_andrews=za_result,
            conclusion="I(0)",
            explanation="ADF rejects unit root and KPSS does not reject stationarity "
            "→ coherent I(0).",
            za_unverified=za_unverified,
        )

    if not adf_rejects and kpss_rejects:
        # ADF/KPSS say I(1) (unit root).
        # ZA tests null of unit root. If ZA rejects, it says stationary.
        if za_result is not None:
            za_rejects = za_result.p_value < sig
            if za_rejects:
                # Disagreement!
                return StationarityDiagnosis(
                    adf_level=adf_level,
                    kpss_level=kpss_level,
                    adf_first_diff=None,
                    kpss_first_diff=None,
                    zivot_andrews=za_result,
                    conclusion="ambiguous",
                    explanation=(
                        "ADF/KPSS indicate I(1) but Zivot-Andrews finds stationarity "
                        "around a break. Ambiguous."
                    ),
                    za_unverified=za_unverified,
                )

        # Level tests suggest unit root — verify first differences
        diff = np.diff(series)
        if len(diff) >= 20:  # enough for meaningful diff test
            adf_diff = None
            kpss_diff = None
            try:
                adf_diff = _run_adf(diff, reg, sig)
            except Exception:
                pass
            try:
                kpss_diff = _run_kpss(diff, reg, sig)
            except Exception:
                pass
            if adf_diff is None or kpss_diff is None:
                return StationarityDiagnosis(
                    adf_level=adf_level,
                    kpss_level=kpss_level,
                    adf_first_diff=adf_diff,
                    kpss_first_diff=kpss_diff,
                    zivot_andrews=za_result,
                    conclusion="ambiguous",
                    explanation="First-difference stationarity tests failed to compute.",
                    za_unverified=za_unverified,
                )
            adf_d_rej = adf_diff.p_value is not None and adf_diff.p_value < sig
            kpss_d_rej = kpss_diff.p_value is not None and kpss_diff.p_value < sig

            if adf_d_rej and not kpss_d_rej:
                return StationarityDiagnosis(
                    adf_level=adf_level,
                    kpss_level=kpss_level,
                    adf_first_diff=adf_diff,
                    kpss_first_diff=kpss_diff,
                    zivot_andrews=za_result,
                    conclusion="I(1)",
                    explanation=(
                        "Level: ADF cannot reject unit root, KPSS rejects stationarity. "
                        "First differences: ADF rejects unit root, "
                        "KPSS does not reject → coherent I(1)."
                    ),
                    za_unverified=za_unverified,
                )
            # Differences still non-stationary → possibly I(2) or ambiguous
            return StationarityDiagnosis(
                adf_level=adf_level,
                kpss_level=kpss_level,
                adf_first_diff=adf_diff,
                kpss_first_diff=kpss_diff,
                zivot_andrews=za_result,
                conclusion="ambiguous",
                explanation="Differences are not coherently I(0). I(2) or ambiguous.",
                za_unverified=za_unverified,
            )
        return StationarityDiagnosis(
            adf_level=adf_level,
            kpss_level=kpss_level,
            adf_first_diff=None,
            kpss_first_diff=None,
            zivot_andrews=za_result,
            conclusion="ambiguous",
            explanation="Insufficient sample to test first differences.",
        )

    # Inconclusive (e.g., both reject or neither rejects)
    return StationarityDiagnosis(
        adf_level=adf_level,
        kpss_level=kpss_level,
        adf_first_diff=adf_diff,
        kpss_first_diff=kpss_diff,
        zivot_andrews=za_result,
        conclusion="ambiguous",
        explanation="ADF and KPSS yield conflicting integration orders.",
        za_unverified=za_unverified,
    )
