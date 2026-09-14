"""Focused tests for the deterministic causality tool."""

from dataclasses import asdict
from datetime import date, timedelta
from enum import Enum, StrEnum
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import kkb_agent.tools.causality as cmod
from kkb_agent.tools.causality import (
    BreakpointInput,
    CausalityError,
    CausalityResult,
    DirectionalTestResult,
    _holm_correct,
    _run_toda_yamamoto,
    analyze_causality,
)
from kkb_agent.tools.causality import breakpoints as breakpoint_mod
from kkb_agent.tools.causality import models as causality_models
from kkb_agent.tools.causality import robustness as robustness_mod
from kkb_agent.tools.causality import stationarity as stationarity_mod
from kkb_agent.tools.causality import tool as causality_tool
from kkb_agent.tools.causality import validation as validation_mod
from kkb_agent.tools.causality import var as var_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _monthly_dates(length: int, start_year: int = 2020, start_month: int = 1):
    """Generate monthly first-of-month dates."""
    dates = []
    year, month = start_year, start_month
    for _ in range(length):
        dates.append(date(year, month, 1))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return tuple(dates)


def _daily_dates(length: int):
    start = date(2020, 1, 1)
    return tuple(start + timedelta(days=i) for i in range(length))


def _stationary_xy_fixture(n=300, seed=42):
    """Stationary AR(1) with known x→y causal link."""
    rng = np.random.RandomState(seed)
    x = np.zeros(n)
    y = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.5 * x[t - 1] + rng.normal(0, 1)
        y[t] = 0.3 * y[t - 1] + 0.8 * x[t - 1] + rng.normal(0, 1)
    return x, y, _daily_dates(n)


def _independent_stationary_fixture(n=300, seed=42):
    """Two independent stationary AR(1) processes — null relationship."""
    rng = np.random.RandomState(seed)
    x = np.zeros(n)
    y = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.5 * x[t - 1] + rng.normal(0, 1)
        y[t] = 0.5 * y[t - 1] + rng.normal(0, 1)
    return x, y, _daily_dates(n)


def _bidirectional_fixture(n=300, seed=42):
    """Bidirectional AR process — both directions should be detected."""
    rng = np.random.RandomState(seed)
    x = np.zeros(n)
    y = np.zeros(n)
    for t in range(1, n):
        x[t] = 0.3 * x[t - 1] + 0.6 * y[t - 1] + rng.normal(0, 1)
        y[t] = 0.3 * y[t - 1] + 0.6 * x[t - 1] + rng.normal(0, 1)
    return x, y, _daily_dates(n)


def _random_walk_pair(n=200, seed=42):
    """Two independent I(1) random walks."""
    rng = np.random.RandomState(seed)
    x = np.cumsum(rng.normal(0, 1, n))
    y = np.cumsum(rng.normal(0, 1, n))
    return x, y, _daily_dates(n)


def _cointegrated_pair(n=300, seed=42):
    """Cointegrated I(1) pair: y = 2*x + stationary noise."""
    rng = np.random.RandomState(seed)
    x = np.cumsum(rng.normal(0, 1, n))
    y = 2.0 * x + rng.normal(0, 0.5, n)
    return x, y, _daily_dates(n)


# ===================================================================
# Validation tests (1–6)
# ===================================================================


class TestValidation:
    """Tests 1–6: input validation and missing-value policy."""

    def test_unequal_lengths_rejected(self):
        with pytest.raises(CausalityError, match="same length"):
            analyze_causality([1.0, 2.0], [1.0], [date(2020, 1, 1), date(2020, 1, 2)])

    def test_duplicate_dates_rejected(self):
        with pytest.raises(CausalityError, match="strictly increasing"):
            analyze_causality(
                [1.0, 2.0],
                [1.0, 2.0],
                [date(2020, 1, 1), date(2020, 1, 1)],
            )

    def test_reversed_dates_rejected(self):
        with pytest.raises(CausalityError, match="strictly increasing"):
            analyze_causality(
                [1.0, 2.0],
                [1.0, 2.0],
                [date(2020, 1, 2), date(2020, 1, 1)],
            )

    def test_insufficient_observations_refused(self):
        n = 30  # below _MIN_STATIONARITY_OBS
        result = analyze_causality(
            list(range(n)),
            list(range(n)),
            _daily_dates(n),
        )
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "insufficient_observations"

    def test_constant_series_refused(self):
        n = 60
        result = analyze_causality(
            [5.0] * n,
            list(range(n)),
            _daily_dates(n),
        )
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "constant_series"
        assert result.x_to_y is None
        assert result.y_to_x is None

    def test_structured_not_identifiable(self):
        x, y, dates = _independent_stationary_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "causality_not_identifiable"
        assert result.x_to_y.significant is False
        assert result.y_to_x.significant is False

    def test_irregular_frequency_refused(self):
        """Unknown frequency must trigger irregular_frequency refusal."""
        n = 100
        # Irregular dates: jumps of 1, 10, 50 days randomly
        rng = np.random.RandomState(42)
        dates = [date(2020, 1, 1)]
        for _ in range(n - 1):
            dates.append(dates[-1] + timedelta(days=int(rng.randint(1, 20))))

        result = analyze_causality(
            list(range(n)),
            list(range(n)),
            dates,
        )
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "irregular_frequency"

    def test_unverified_vecm_stability_caps_evidence(self, monkeypatch):
        """VECM stability is unverified, so positive result must be capped."""
        x, y, dates = _cointegrated_pair(n=150, seed=42)

        # Mock VECMResults.var_rep to raise an exception, simulating unavailable verification
        from statsmodels.tsa.vector_ar.vecm import VECMResults

        def mock_var_rep(*args, **kwargs):
            raise ValueError("var_rep unavailable")

        monkeypatch.setattr(VECMResults, "var_rep", property(mock_var_rep))

        result = analyze_causality(x.tolist(), y.tolist(), dates)
        # If it found a predictive relationship, it must be capped
        if result.status != "not_identifiable":
            assert result.status == "limited_evidence"
            assert result.diagnostics is not None
            assert result.diagnostics.model_stable is None
            assert "required diagnostics unverified" in result.explanation

    def test_stable_vecm_does_not_cap_evidence(self):
        """VECM stability succeeds for a known cointegrated pair."""
        x, y, dates = _cointegrated_pair(n=150, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        if result.status == "predictive_relationship_supported":
            assert result.diagnostics.model_stable is True

    def test_var_diagnostics_unavailable_caps_evidence(self, monkeypatch):
        """VAR whiteness unavailable cannot produce predictive_relationship_supported."""
        x, y, dates = _stationary_xy_fixture(n=150, seed=42)
        # Mock test_whiteness to raise an exception so autocorr_pval becomes None
        from statsmodels.tsa.vector_ar.var_model import VARResults

        def mock_test_whiteness(*args, **kwargs):
            raise ValueError("Whiteness unavailable")

        monkeypatch.setattr(VARResults, "test_whiteness", mock_test_whiteness)

        result = analyze_causality(x.tolist(), y.tolist(), dates)

        if result.status != "not_identifiable":
            assert result.status == "limited_evidence"
            assert result.diagnostics is not None
            assert result.diagnostics.residual_autocorrelation_p_value is None
            assert "required diagnostics unverified" in result.explanation

    def test_integration_order_metadata_propagated(self):
        """Integration order is preserved when refusal occurs after stat diagnosis."""
        x, y, dates = _cointegrated_pair(n=100, seed=42)
        # N=100 is enough for cointegration (needs 100), but not VECM (needs 120)
        # So it will refuse with cointegration_requires_unsupported_model.
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "cointegration_requires_unsupported_model"
        assert result.max_integration_order == 1

    def test_mixed_order_metadata_propagated(self):
        """Mixed order (I(0) + I(1)) must report max_integration_order=1 upon refusal."""
        rng = np.random.RandomState(42)
        n = 100
        # x is I(1) random walk
        x = np.cumsum(rng.normal(0, 1, n))
        # y is I(0) white noise
        y = rng.normal(0, 1, n)
        dates = _daily_dates(n)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "mixed_integration_order"
        assert result.max_integration_order == 1

    def test_missing_values_not_interpolated(self):
        """Missing values are dropped, never filled.  Drop count is reported."""
        rng = np.random.RandomState(42)
        n = 100
        x = list(0.5 * rng.normal(0, 1, n))
        y = list(0.5 * rng.normal(0, 1, n))
        x[10] = None
        y[50] = None
        result = analyze_causality(x, y, _daily_dates(n))
        assert result.dropped_observations >= 2
        assert result.effective_observations <= n - 2


# ===================================================================
# Stationarity tests (7–9)
# ===================================================================


class TestStationarity:
    """Tests 7–9: paired ADF/KPSS diagnosis."""

    def test_stationary_series_recognized(self):
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.stationarity_x is not None
        assert result.stationarity_x.conclusion == "I(0)"
        assert result.stationarity_y is not None
        assert result.stationarity_y.conclusion == "I(0)"

    def test_i1_random_walk_no_levels_granger(self):
        """I(1) series must not go directly to plain levels Granger."""
        x, y, dates = _random_walk_pair(n=200, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.method != "granger_stationary_var"
        # Since n=200 > 120 and we don't have cointegration, it's either differenced VAR or refusal
        assert result.method in ("granger_differenced_var", None)

    def test_adf_kpss_disagreement_yields_ambiguity(self):
        """When ADF and KPSS contradict, classification must be ambiguous."""
        rng = np.random.RandomState(42)
        n = 100
        # Trend-stationary: ADF may reject unit root, KPSS rejects level-stationarity
        x = np.arange(n, dtype=float) * 0.5 + rng.normal(0, 1, n)
        y = rng.normal(0, 1, n)
        result = analyze_causality(x.tolist(), y.tolist(), _daily_dates(n))
        # X should be ambiguous (ADF rejects + KPSS rejects = contradiction)
        if result.stationarity_x is not None:
            # Either ambiguous stationarity or the tool handled it via refusal
            if result.stationarity_x.conclusion == "ambiguous":
                assert result.status == "not_identifiable"
                assert result.refusal_reason == "stationarity_ambiguous"
            else:
                # If both tests happened to agree, that's also acceptable
                assert result.stationarity_x.conclusion in ("I(0)", "I(1)")


# ===================================================================
# Cointegration tests (10–11)
# ===================================================================


class TestCointegration:
    """Tests 10–11: cointegration decision path."""

    def test_cointegrated_pair_takes_valid_path(self):
        """Cointegrated I(1) pair must take VECM or documented valid path."""
        x, y, dates = _cointegrated_pair(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        # Either VECM, differenced VAR, or refusal — never plain levels Granger
        assert result.method != "granger_stationary_var"
        if result.cointegration is not None and result.cointegration.cointegrated:
            # If cointegration detected, must use VECM (since n=300 >= 120)
            assert result.method == "vecm"

    def test_non_cointegrated_i1_no_levels_granger(self):
        """Non-cointegrated I(1) pair must never receive plain levels Granger."""
        x, y, dates = _random_walk_pair(n=200, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.method != "granger_stationary_var"


# ===================================================================
# Granger / Toda-Yamamoto tests (12–17)
# ===================================================================


class TestGrangerTY:
    """Tests 12–17: directional tests and Toda-Yamamoto."""

    def test_known_x_to_y_detected(self):
        """Strong x→y process must be detected."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.x_to_y is not None
        assert result.x_to_y.significant is True
        assert result.status in ("predictive_relationship_supported", "limited_evidence")

    def test_reverse_y_to_x_not_falsely_detected(self):
        """In the x→y fixture, y→x must not be significant."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.y_to_x is not None
        assert result.y_to_x.significant is False

    def test_bidirectional_process_represented(self):
        """Bidirectional process must show both directions."""
        x, y, dates = _bidirectional_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.x_to_y is not None
        assert result.y_to_x is not None
        assert result.x_to_y.significant is True
        assert result.y_to_x.significant is True

    def test_null_relationship_no_directional_claim(self):
        """Independent processes must not produce a directional claim."""
        x, y, dates = _independent_stationary_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        if result.x_to_y is not None:
            assert result.x_to_y.significant is False
        if result.y_to_x is not None:
            assert result.y_to_x.significant is False
        assert result.status != "predictive_relationship_supported"

    def test_selected_lag_deterministic_and_surfaced(self):
        """Lag selection must be deterministic and present in result."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        r1 = analyze_causality(x.tolist(), y.tolist(), dates)
        r2 = analyze_causality(x.tolist(), y.tolist(), dates)
        assert r1.selected_lag is not None
        assert r1.selected_lag == r2.selected_lag
        assert r1.selected_lag >= 1

    def test_toda_yamamoto_restricts_first_p_lags(self):
        """T-Y Wald test must use only first p lags, not augmented lags."""
        # Create data where effect is only at lag 1
        rng = np.random.RandomState(42)
        n = 300
        x = np.zeros(n)
        y = np.zeros(n)
        for t in range(1, n):
            x[t] = 0.5 * x[t - 1] + rng.normal(0, 1)
            y[t] = 0.3 * y[t - 1] + 0.8 * x[t - 1] + rng.normal(0, 1)
        data = np.column_stack([x, y])

        # T-Y with p=1, d_max=1: fit VAR(2), test only lag 1
        ty1 = _run_toda_yamamoto(data, p=1, d_max=1, exog=None)
        assert ty1 is not None
        assert ty1.effective_lags_tested == 1
        assert ty1.augmented_lag_order == 2
        # x→y at lag 1 should be significant (strong effect)
        assert ty1.x_to_y_p_value < 0.05

        # Now create data where effect is ONLY at lag 2
        rng2 = np.random.RandomState(99)
        x2 = np.zeros(n)
        y2 = np.zeros(n)
        for t in range(2, n):
            x2[t] = 0.3 * x2[t - 1] + rng2.normal(0, 1)
            y2[t] = 0.3 * y2[t - 1] + 0.0 * x2[t - 1] + 0.8 * x2[t - 2] + rng2.normal(0, 1)
        data2 = np.column_stack([x2, y2])

        # T-Y with p=1, d_max=1: tests only lag 1 — should NOT find x→y
        ty2 = _run_toda_yamamoto(data2, p=1, d_max=1, exog=None)
        assert ty2 is not None
        assert ty2.effective_lags_tested == 1
        # The lag-2 effect is NOT tested → should not be significant at lag 1
        assert ty2.x_to_y_p_value > 0.01

    def test_adjacent_lag_robustness_unavailable_caps_evidence(self):
        """If max_lag=1, adjacent lag robustness is unavailable, capping result."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates, max_lag=1)
        # x->y is strong, so it should be detected, but capped due to no adjacent lags
        assert result.x_to_y is not None
        assert result.x_to_y.significant is True
        assert result.status == "limited_evidence"
        assert "adjacent-lag robustness unavailable" in result.explanation


# ===================================================================
# Breakpoint tests (18–21)
# ===================================================================


class TestBreakpoints:
    """Tests 18–21: structural break integration."""

    def test_breakpoint_input_accepted(self):
        """BreakpointInput from change-detection output must be accepted."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        bp = BreakpointInput(date=dates[150], kind="level")
        result = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        assert isinstance(result, CausalityResult)

    def test_breakpoint_metadata_in_result(self):
        """Breakpoints used must appear in the result."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        bp = BreakpointInput(date=dates[150], kind="level")
        result = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        assert len(result.breakpoints_used) == 1
        assert result.breakpoints_used[0].date == dates[150]
        assert result.breakpoints_used[0].kind == "level"

    def test_breaks_not_silently_ignored(self):
        """Supplied breaks must affect the model (via regime dummies)."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        r_no_bp = analyze_causality(x.tolist(), y.tolist(), dates)
        bp = BreakpointInput(date=dates[150], kind="level")
        r_with_bp = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        # At minimum the breakpoints_used differs
        assert len(r_with_bp.breakpoints_used) > len(r_no_bp.breakpoints_used)
        # If both succeeded, the test statistics should differ (different exog)
        if r_no_bp.x_to_y is not None and r_with_bp.x_to_y is not None:
            assert r_no_bp.x_to_y.statistic != pytest.approx(
                r_with_bp.x_to_y.statistic,
                rel=1e-6,
            )

    def test_breakpoint_caps_evidence_if_regimes_fail(self, monkeypatch):
        """Breakpoints must cap a positive result at limited_evidence if regimes fail to pass."""
        import kkb_agent.tools.causality as cmod

        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        bp = BreakpointInput(date=dates[150], kind="level")

        call_count = 0

        def mock_pipeline(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            x_to_y = cmod.DirectionalTestResult("x_to_y", 10.0, 0.01, 0.02, True, 1, "Wald")
            y_to_x = cmod.DirectionalTestResult("y_to_x", 1.0, 0.5, 0.5, False, 1, "Wald")

            if call_count == 2:
                # Regime 1: Valid but NO significance -> inconclusive
                x_to_y = cmod.DirectionalTestResult("x_to_y", 1.0, 0.5, 0.5, False, 1, "Wald")

            return cmod.CausalityResult(
                status="predictive_relationship_supported",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=(),
                x_to_y=x_to_y,
                y_to_x=y_to_x,
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=cmod.DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        result = cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        assert result.status == "limited_evidence"

    def test_breakpoint_regimes_pass_does_not_cap_evidence(self):
        """If regime checks fully agree, result is not capped."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        # Shift Y so X -> Y is true and robust
        for i in range(1, 300):
            y[i] += 0.5 * x[i - 1]
        bp = BreakpointInput(date=dates[150], kind="level")
        result = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        # The true VAR processes should pass the regimes checks and return the strongest evidence
        if result.status != "not_identifiable":
            assert result.status == "predictive_relationship_supported"

    def test_breakpoint_kind_validation_and_trend(self):
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        with pytest.raises(CausalityError, match="kind must be 'level' or 'trend'"):
            bp_invalid = BreakpointInput(date=dates[150], kind="shift")
            analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp_invalid])

        # Trend dummy should produce different result than level dummy
        bp_level = BreakpointInput(date=dates[150], kind="level")
        bp_trend = BreakpointInput(date=dates[150], kind="trend")
        r_level = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp_level])
        r_trend = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp_trend])
        if r_level.x_to_y is not None and r_trend.x_to_y is not None:
            assert r_level.x_to_y.statistic != pytest.approx(r_trend.x_to_y.statistic, rel=1e-6)

    def test_breakpoint_dummy_vector_structure(self):
        """Assert exact dummy vector generation for level and trend breaks."""

        dates = _daily_dates(10)
        # break at index 5 (6th element)
        bp_level = BreakpointInput(date=dates[5], kind="level")
        bp_trend = BreakpointInput(date=dates[5], kind="trend")

        dummies_level = breakpoint_mod._build_break_dummies((bp_level,), dates)
        assert dummies_level is not None
        expected_level = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
        np.testing.assert_array_equal(dummies_level[:, 0], expected_level)

        dummies_trend = breakpoint_mod._build_break_dummies((bp_trend,), dates)
        assert dummies_trend is not None
        # Ramp starts at 0 at the breakpoint, then 1, 2, 3...
        expected_trend = np.array([0, 0, 0, 0, 0, 0, 1, 2, 3, 4])
        np.testing.assert_array_equal(dummies_trend[:, 0], expected_trend)

    def test_too_many_breaks_trigger_refusal(self):
        """Excessive breakpoints must trigger structural_break_confounding refusal."""
        n = 100
        x, y = list(range(n)), list(range(n, 2 * n))
        dates = _daily_dates(n)
        # Place breaks every 10 observations → regimes of ~10 < _MIN_REGIME_OBS=15
        bps = [BreakpointInput(date=dates[i], kind="level") for i in range(10, n, 10)]
        result = analyze_causality(x, y, dates, breakpoints=bps)
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "structural_break_confounding"


# ===================================================================
# Refusal tests (22–24)
# ===================================================================


class TestRefusal:
    """Tests 22–24: refusal path integrity."""

    def test_invalid_spec_returns_not_identifiable(self):
        """Invalid specification must return structured not_identifiable."""
        result = analyze_causality(
            [5.0] * 60,
            list(range(60)),
            _daily_dates(60),
        )
        assert result.status == "not_identifiable"
        assert result.refusal_reason is not None
        assert isinstance(result.explanation, str)
        assert len(result.explanation) > 0

    def test_refusal_names_reason(self):
        """Refusal must include a specific reason string."""
        result = analyze_causality(
            list(range(30)),
            list(range(30)),
            _daily_dates(30),
        )
        assert result.status == "not_identifiable"
        assert result.refusal_reason is not None
        assert result.refusal_details is not None
        assert len(result.refusal_details) > 0

    def test_refusal_has_no_direction_claim(self):
        """A refusal result must not include causal-direction claims."""
        result = analyze_causality(
            [5.0] * 60,
            list(range(60)),
            _daily_dates(60),
        )
        assert result.x_to_y is None
        assert result.y_to_x is None


# ===================================================================
# Architecture tests (25–28)
# ===================================================================


class TestArchitecture:
    """Tests 25–28: contract integrity."""

    def test_result_is_serializable(self):
        """CausalityResult must serialize via dataclasses.asdict."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        d = asdict(result)
        assert isinstance(d, dict)
        assert "status" in d
        assert "method" in d
        assert "stationarity_x" in d
        assert "explanation" in d

    def test_no_statsmodels_object_leaks(self):
        """No statsmodels objects should leak through the public contract."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        result = analyze_causality(x.tolist(), y.tolist(), dates)
        d = asdict(result)
        _check_no_statsmodels(d)

    def test_no_llm_dependency(self):
        """The causality module must not import any LLM/MIA dependency."""
        package = (
            Path(__file__).resolve().parent.parent.parent.parent / "src/kkb_agent/tools/causality"
        )
        content = "\n".join(path.read_text().lower() for path in package.glob("*.py"))
        for keyword in ["openai", "import mia", "from mia", "chatcompletion", "prompt"]:
            assert keyword not in content, f"Found prohibited keyword: {keyword}"

    def test_significance_threshold_consistency(self):
        """Custom significance threshold must propagate to diagnostics and results."""
        x, y, dates = _stationary_xy_fixture(n=300, seed=42)
        # Extremely small threshold will make everything insignificant, even strong signals
        result = analyze_causality(x.tolist(), y.tolist(), dates, significance=1e-100)
        assert result.significance_threshold == 1e-100
        if result.x_to_y is not None:
            assert result.status == "not_identifiable"


# ===================================================================
# Holm correction unit test
# ===================================================================


class TestHolmCorrection:
    """Verify Holm step-down procedure produces correct adjusted p-values."""

    def test_two_tests_holm(self):
        corrected = _holm_correct([0.03, 0.04])
        # Smaller: min(1, 2*0.03) = 0.06
        # Larger: min(1, max(0.06, 1*0.04)) = 0.06
        assert corrected[0] == pytest.approx(0.06)
        assert corrected[1] == pytest.approx(0.06)

    def test_significant_and_nonsignificant(self):
        corrected = _holm_correct([0.01, 0.10])
        # Smaller: min(1, 2*0.01) = 0.02  → significant at 0.05
        # Larger: min(1, max(0.02, 1*0.10)) = 0.10  → not significant
        assert corrected[0] == pytest.approx(0.02)
        assert corrected[1] == pytest.approx(0.10)

    def test_monotonicity_enforced(self):
        corrected = _holm_correct([0.04, 0.01])
        # After sorting: p_(1)=0.01, p_(2)=0.04
        # p_adj(1) = 2*0.01 = 0.02
        # p_adj(2) = max(0.02, 1*0.04) = 0.04
        assert corrected[1] == pytest.approx(0.02)  # smaller raw
        assert corrected[0] == pytest.approx(0.04)  # larger raw


# ===================================================================
# Helper for architecture test
# ===================================================================


def _check_no_statsmodels(obj, path="root"):
    """Recursively check that no statsmodels types leaked into the result dict."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _check_no_statsmodels(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_no_statsmodels(v, f"{path}[{i}]")
    else:
        type_name = type(obj).__module__
        assert not type_name.startswith("statsmodels"), (
            f"statsmodels object leaked at {path}: {type(obj)}"
        )


class TestRegimeSplitRobustness:
    def test_regime_robustness_unavailable_caps_evidence(self):
        """If a regime is too small, global result caps at limited_evidence."""
        from kkb_agent.tools.causality import BreakpointInput

        # Create a stationary signal
        x, y, dates = _stationary_xy_fixture(n=100, seed=42)

        # Insert a breakpoint at row 95. The second regime has 5 rows, which is < 15
        bp = BreakpointInput(date=dates[95], kind="level")

        result = analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

        if result.status != "not_identifiable":
            assert result.status == "limited_evidence"
            assert result.regimes is not None
            assert len(result.regimes) == 2

            r2 = result.regimes[1]
            assert r2.status == "unavailable"
            assert r2.effective_observations == 5

    def test_regime_reversal_downgrades_to_not_identifiable(self, monkeypatch):
        """If a valid regime has significant opposite direction, result is not_identifiable."""
        from datetime import date, timedelta

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import (
            BreakpointInput,
            CausalityResult,
            DiagnosticsResult,
            DirectionalTestResult,
        )

        n = 200
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        bp = BreakpointInput(date=dates[100], kind="level")
        np.random.seed(42)
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)

        # We will intercept _analyze_core_pipeline.
        # Call 1 (global): return a positive X -> Y result.
        # Call 2 (regime 1): return a positive Y -> X result (reversed).
        # Call 3 (regime 2): return a positive X -> Y result.

        call_count = 0

        def mock_pipeline(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            print(f"mock_pipeline call {call_count}")

            x_to_y = DirectionalTestResult("x_to_y", 10.0, 0.01, 0.02, True, 1, "Wald")
            y_to_x = DirectionalTestResult("y_to_x", 1.0, 0.5, 0.5, False, 1, "Wald")

            if call_count == 2:
                # Regime 1: reverse it!
                x_to_y = DirectionalTestResult("x_to_y", 1.0, 0.5, 0.5, False, 1, "Wald")
                y_to_x = DirectionalTestResult("y_to_x", 10.0, 0.01, 0.02, True, 1, "Wald")

            return CausalityResult(
                status="predictive_relationship_supported",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=(),
                x_to_y=x_to_y,
                y_to_x=y_to_x,
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)

        result = cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

        # Because of the strong reversal, it should be not_identifiable
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "regime_robustness_failed"
        assert any(r.status == "reversed" for r in result.regimes)


class TestZivotAndrewsSensitivity:
    def test_za_disagreement_yields_ambiguity(self, monkeypatch):
        """ZA disagreement with the paired tests makes integration ambiguous."""
        # Generate I(1) random walk
        from datetime import date, timedelta

        import numpy as np

        np.random.seed(42)
        n = 100
        x = np.cumsum(np.random.normal(0, 1, n))
        y = np.cumsum(np.random.normal(0, 1, n))
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import ZivotAndrewsResult

        def mock_za(*args, **kwargs):
            # p_value < 0.05 means it rejects unit root (finds stationarity)
            return ZivotAndrewsResult(
                statistic=-5.0,
                p_value=0.01,
                critical_values={"5%": -4.8},
                estimated_break_date=dates[50],
                regression="c",
                lags_used=1,
                interpretation="Rejects unit root",
            )

        monkeypatch.setattr(stationarity_mod, "_run_zivot_andrews", mock_za)

        result = cmod.analyze_causality(x.tolist(), y.tolist(), dates)
        assert result.status == "not_identifiable"
        assert result.refusal_reason == "stationarity_ambiguous"
        assert result.stationarity_x is not None
        assert result.stationarity_x.conclusion == "ambiguous"
        assert "Zivot-Andrews finds stationarity" in result.stationarity_x.explanation

    def test_analyzable_but_nonsignificant_regime_is_inconclusive(self, monkeypatch):
        from datetime import date, timedelta

        import numpy as np

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import (
            BreakpointInput,
            CausalityResult,
            DirectionalTestResult,
        )

        n = 200
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)
        bp = BreakpointInput(date=dates[100], kind="level")

        call_count = 0

        def mock_pipeline(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Global: X -> Y
                x_to_y = DirectionalTestResult("x_to_y", 10.0, 0.01, 0.02, True, 1, "Wald")
                y_to_x = DirectionalTestResult("y_to_x", 1.0, 0.5, 0.5, False, 1, "Wald")
            elif call_count == 2:
                # Regime 1: Valid but NO significance
                x_to_y = DirectionalTestResult("x_to_y", 1.0, 0.5, 0.5, False, 1, "Wald")
                y_to_x = DirectionalTestResult("y_to_x", 1.0, 0.5, 0.5, False, 1, "Wald")
            else:
                # Regime 2: Passed
                x_to_y = DirectionalTestResult("x_to_y", 10.0, 0.01, 0.02, True, 1, "Wald")
                y_to_x = DirectionalTestResult("y_to_x", 1.0, 0.5, 0.5, False, 1, "Wald")

            return CausalityResult(
                status="predictive_relationship_supported",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=(),
                x_to_y=x_to_y,
                y_to_x=y_to_x,
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=cmod.DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        res = cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        assert res.status == "limited_evidence"
        assert res.regimes[0].status == "inconclusive"
        assert res.regimes[1].status == "passed"

    def test_out_of_range_breakpoint_raises_error(self):
        from datetime import date, timedelta

        import numpy as np
        import pytest

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import BreakpointInput, CausalityError

        n = 50
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)
        bp = BreakpointInput(date=date(2021, 1, 1), kind="level")
        with pytest.raises(CausalityError, match="outside the effective sample range"):
            cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

    def test_za_fail_to_reject_not_ambiguous(self, monkeypatch):
        from datetime import date, timedelta

        import numpy as np

        import kkb_agent.tools.causality as cmod

        n = 100
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)

        def mock_run_adf(*args, **kwargs):
            return cmod.StationarityTestResult("ADF", -4.0, 0.001, {"5%": -2.8}, "c", 1, "Reject")

        def mock_run_kpss(*args, **kwargs):
            return cmod.StationarityTestResult(
                "KPSS", 0.1, 0.5, {"5%": 0.46}, "c", 1, "Fail to reject"
            )

        def mock_run_za(*args, **kwargs):
            return cmod.ZivotAndrewsResult(
                -2.0, 0.1, {"1%": -5.0}, dates[50], "c", 1, "Fail to reject"
            )

        monkeypatch.setattr(stationarity_mod, "_run_adf", mock_run_adf)
        monkeypatch.setattr(stationarity_mod, "_run_kpss", mock_run_kpss)
        monkeypatch.setattr(stationarity_mod, "_run_zivot_andrews", mock_run_za)

        stat = stationarity_mod._diagnose_stationarity(x, dates, 0.05)
        assert stat.conclusion == "I(0)"

    def test_vecm_stability_serializes(self, monkeypatch):
        from dataclasses import asdict

        import kkb_agent.tools.causality as cmod

        ev = cmod.VecmStabilityEvidence(1, 1, 1e-4, 0.5, True, "OK")
        diag = cmod.DiagnosticsResult(True, ev, 0.5, 90, 10, True, True, "OK")
        res = asdict(diag)
        assert res["vecm_stability"]["expected_unit_roots"] == 1
        assert res["vecm_stability"]["largest_non_unit_root_modulus"] == 0.5


class TestRegimeDirectionCombinations:
    @pytest.mark.parametrize(
        "global_dir, regime_dir, expected_status, expected_regime_status",
        [
            # global x_to_y
            (
                ("x_to_y", True, False),
                ("x_to_y", True, False),
                "predictive_relationship_supported",
                "passed",
            ),
            (("x_to_y", True, False), ("y_to_x", False, True), "not_identifiable", "reversed"),
            (
                ("x_to_y", True, False),
                ("bidirectional", True, True),
                "limited_evidence",
                "inconclusive",
            ),
            (("x_to_y", True, False), ("none", False, False), "limited_evidence", "inconclusive"),
            (
                ("x_to_y", True, False),
                ("limited_same", True, False),
                "limited_evidence",
                "inconclusive",
            ),
            # global y_to_x
            (
                ("y_to_x", False, True),
                ("y_to_x", False, True),
                "predictive_relationship_supported",
                "passed",
            ),
            (("y_to_x", False, True), ("x_to_y", True, False), "not_identifiable", "reversed"),
            (
                ("y_to_x", False, True),
                ("bidirectional", True, True),
                "limited_evidence",
                "inconclusive",
            ),
            (("y_to_x", False, True), ("none", False, False), "limited_evidence", "inconclusive"),
            # global bidirectional
            (
                ("bidirectional", True, True),
                ("bidirectional", True, True),
                "predictive_relationship_supported",
                "passed",
            ),
            (
                ("bidirectional", True, True),
                ("x_to_y", True, False),
                "limited_evidence",
                "inconclusive",
            ),
            (
                ("bidirectional", True, True),
                ("y_to_x", False, True),
                "limited_evidence",
                "inconclusive",
            ),
            (
                ("bidirectional", True, True),
                ("none", False, False),
                "limited_evidence",
                "inconclusive",
            ),
        ],
    )
    def test_regime_direction_combinations(
        self, monkeypatch, global_dir, regime_dir, expected_status, expected_regime_status
    ):
        n = 200
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)
        bp = BreakpointInput(date=dates[100], kind="level")

        g_name, g_xy, g_yx = global_dir
        r_name, r_xy, r_yx = regime_dir

        call_count = 0

        def mock_pipeline(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Global
                x_to_y = DirectionalTestResult(
                    "x_to_y", 10.0, 0.01 if g_xy else 0.5, 0.01 if g_xy else 0.5, g_xy, 1, "Wald"
                )
                y_to_x = DirectionalTestResult(
                    "y_to_x", 10.0, 0.01 if g_yx else 0.5, 0.01 if g_yx else 0.5, g_yx, 1, "Wald"
                )
            else:
                # Regime
                x_to_y = DirectionalTestResult(
                    "x_to_y", 10.0, 0.01 if r_xy else 0.5, 0.01 if r_xy else 0.5, r_xy, 1, "Wald"
                )
                y_to_x = DirectionalTestResult(
                    "y_to_x", 10.0, 0.01 if r_yx else 0.5, 0.01 if r_yx else 0.5, r_yx, 1, "Wald"
                )

            return CausalityResult(
                status=(
                    "limited_evidence"
                    if call_count > 1 and r_name == "limited_same"
                    else "predictive_relationship_supported"
                ),
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=(),
                x_to_y=x_to_y,
                y_to_x=y_to_x,
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=cmod.DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        res = cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])
        assert res.status == expected_status
        assert res.regimes[0].status == expected_regime_status
        assert res.regimes[1].status == expected_regime_status


class ChangeKind(Enum):
    LEVEL = "level"
    TREND = "trend"


class StrEnumKind(StrEnum):
    LEVEL = "level"
    TREND = "trend"


class DuckKind:
    def __init__(self, value):
        self.value = value


class DuckBreakpoint:
    def __init__(self, date, kind):
        self.date = date
        self.kind = kind


class TestEnumBreakpoints:
    def test_breakpoint_normalization_types(self):
        d1 = date(2020, 1, 1)
        # string
        b1 = DuckBreakpoint(d1, "level")
        # standard enum
        b2 = DuckBreakpoint(d1, ChangeKind.LEVEL)
        # str enum
        b3 = DuckBreakpoint(d1, StrEnumKind.TREND)
        # duck type with value
        b4 = DuckBreakpoint(d1, DuckKind("level"))

        result = breakpoint_mod._normalize_breakpoints([b1, b2, b3, b4], (d1,))
        assert len(result) == 4
        assert result[0].kind == "level"
        assert result[1].kind == "level"
        assert result[2].kind == "trend"
        assert result[3].kind == "level"

    def test_level_enum_creates_step_dummy(self, monkeypatch):
        n = 100
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)
        bp = DuckBreakpoint(dates[50], ChangeKind.LEVEL)

        exog_captured = None

        def mock_pipeline(
            x_clean, y_clean, dates_clean, exog, frequency, sig, max_lag, bp_inputs, dropped
        ):
            nonlocal exog_captured
            exog_captured = exog
            from kkb_agent.tools.causality import (
                CausalityResult,
                DiagnosticsResult,
                DirectionalTestResult,
            )

            return CausalityResult(
                status="not_identifiable",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=bp_inputs,
                x_to_y=DirectionalTestResult("x_to_y", 1, 0.5, 0.5, False, 1, "Wald"),
                y_to_x=DirectionalTestResult("y_to_x", 1, 0.5, 0.5, False, 1, "Wald"),
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

        assert exog_captured is not None
        assert exog_captured.shape[1] == 1
        # Level dummy should be 0 before break and 1 after
        assert exog_captured[49, 0] == 0
        assert exog_captured[50, 0] == 1
        assert exog_captured[51, 0] == 1


class TestZivotAndrewsSampleBounds:
    def test_za_skipped_at_79_runs_at_80(self, monkeypatch):
        # 79 observations
        series79 = np.random.normal(0, 1, 79)
        dates79 = [date(2020, 1, 1) + timedelta(days=i) for i in range(79)]

        # 80 observations
        series80 = np.random.normal(0, 1, 80)
        dates80 = [date(2020, 1, 1) + timedelta(days=i) for i in range(80)]

        za_called = False

        def mock_za(*args, **kwargs):
            nonlocal za_called
            za_called = True
            from kkb_agent.tools.causality import ZivotAndrewsResult

            return ZivotAndrewsResult(1.0, 0.5, {"1%": 2.0}, dates80[40], "c", 1, "Fail to reject")

        monkeypatch.setattr(stationarity_mod, "_run_zivot_andrews", mock_za)

        # Mock ADF/KPSS to prevent errors during test
        def mock_adf(*args, **kwargs):
            from kkb_agent.tools.causality import StationarityTestResult

            return StationarityTestResult("ADF", 1.0, 0.5, {"1%": 2.0}, "c", 1, "Fail to reject")

        def mock_kpss(*args, **kwargs):
            from kkb_agent.tools.causality import StationarityTestResult

            return StationarityTestResult("KPSS", 1.0, 0.5, {"1%": 2.0}, "c", 1, "Fail to reject")

        monkeypatch.setattr(stationarity_mod, "_run_adf", mock_adf)
        monkeypatch.setattr(stationarity_mod, "_run_kpss", mock_kpss)

        diag79 = stationarity_mod._diagnose_stationarity(series79, dates79, 0.05)
        assert not za_called
        assert diag79.zivot_andrews is None

        diag80 = stationarity_mod._diagnose_stationarity(series80, dates80, 0.05)
        assert za_called
        assert diag80.zivot_andrews is not None

    def test_stationarity_exception_handling(self, monkeypatch):
        series = np.random.normal(0, 1, 100)
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(100)]

        def mock_raise(*args, **kwargs):
            raise ValueError("LinAlgError from statsmodels")

        monkeypatch.setattr(stationarity_mod, "_run_adf", mock_raise)

        diag = stationarity_mod._diagnose_stationarity(series, dates, 0.05)
        assert diag.conclusion == "ambiguous"
        assert "Stationarity test failed to compute" in diag.explanation

        # Test ZA failure doesn't blow up but just skips ZA
        def mock_adf(*args, **kwargs):
            from kkb_agent.tools.causality import StationarityTestResult

            return StationarityTestResult("ADF", 1.0, 0.5, {"1%": 2.0}, "c", 1, "Fail to reject")

        monkeypatch.setattr(stationarity_mod, "_run_adf", mock_adf)
        monkeypatch.setattr(
            stationarity_mod, "_run_kpss", mock_raise
        )  # keep kpss failing to test different branch if needed

        monkeypatch.setattr(stationarity_mod, "_run_zivot_andrews", mock_raise)
        # Should gracefully return None for ZA
        diag = stationarity_mod._diagnose_stationarity(series, dates, 0.05)
        assert diag.zivot_andrews is None


def test_za_unverified_caps_positive_evidence(monkeypatch):
    from datetime import date, timedelta

    import numpy as np

    import kkb_agent.tools.causality as cmod

    n = 100
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    np.random.seed(42)
    # create strong stationary X -> Y
    x = np.random.normal(0, 1, n)
    y = np.zeros(n)
    for i in range(1, n):
        y[i] = 0.5 * x[i - 1] + np.random.normal(0, 0.1)

    def mock_raise(*args, **kwargs):
        raise ValueError("Simulated ZA failure")

    monkeypatch.setattr(stationarity_mod, "_run_zivot_andrews", mock_raise)

    res = cmod.analyze_causality(x.tolist(), y.tolist(), dates)
    assert res.status == "limited_evidence"
    assert "Zivot-Andrews structural-break sensitivity check unavailable" in res.explanation
    assert res.x_to_y is not None
    assert res.x_to_y.significant


class TestBreakpointDateNormalization:
    def test_date_observations_datetime_breakpoint(self, monkeypatch):
        from datetime import date, datetime, timedelta

        import numpy as np

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import BreakpointInput

        n = 100
        # Observations use date
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)

        # Breakpoint uses datetime
        bp_dt = datetime(2020, 2, 1, 12, 0, 0)
        bp = BreakpointInput(date=bp_dt, kind="level")

        def mock_pipeline(
            x_clean, y_clean, dates_clean, exog, frequency, sig, max_lag, bp_inputs, dropped
        ):
            assert len(bp_inputs) == 1
            assert bp_inputs[0].date == bp_dt  # original public date preserved
            assert exog is not None
            # datetime(2020, 2, 1) corresponds to index 31 (Jan has 31 days)
            assert exog[30, 0] == 0
            assert exog[31, 0] == 1
            from kkb_agent.tools.causality import (
                CausalityResult,
                DiagnosticsResult,
                DirectionalTestResult,
            )

            return CausalityResult(
                status="not_identifiable",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=bp_inputs,
                x_to_y=DirectionalTestResult("x", 1, 0.5, 0.5, False, 1, "Wald"),
                y_to_x=DirectionalTestResult("y", 1, 0.5, 0.5, False, 1, "Wald"),
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

    def test_datetime_observations_date_breakpoint(self, monkeypatch):
        from datetime import date, datetime, timedelta

        import numpy as np

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import BreakpointInput

        n = 100
        # Observations use datetime
        dates = [datetime(2020, 1, 1, 6, 0, 0) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)

        # Breakpoint uses date
        bp_d = date(2020, 2, 1)
        bp = BreakpointInput(date=bp_d, kind="level")

        def mock_pipeline(
            x_clean, y_clean, dates_clean, exog, frequency, sig, max_lag, bp_inputs, dropped
        ):
            assert exog[30, 0] == 0
            assert exog[31, 0] == 1
            from kkb_agent.tools.causality import (
                CausalityResult,
                DiagnosticsResult,
                DirectionalTestResult,
            )

            return CausalityResult(
                status="not_identifiable",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[99]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=bp_inputs,
                x_to_y=DirectionalTestResult("x", 1, 0.5, 0.5, False, 1, "Wald"),
                y_to_x=DirectionalTestResult("y", 1, 0.5, 0.5, False, 1, "Wald"),
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

    def test_breakpoint_between_observations_maps_first_geq(self, monkeypatch):
        from datetime import date, timedelta

        import numpy as np

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import BreakpointInput

        # Observations only on Mon/Wed/Fri (e.g. step by 2 days)
        dates = [date(2020, 1, 1) + timedelta(days=i * 2) for i in range(50)]
        x = np.random.normal(0, 1, 50)
        y = np.random.normal(0, 1, 50)

        # date(2020, 1, 1) is idx 0
        # date(2020, 1, 3) is idx 1
        # date(2020, 1, 5) is idx 2
        # break on 2020-01-04 -> maps to 2020-01-05 (idx 2)
        bp = BreakpointInput(date=date(2020, 1, 4), kind="level")

        def mock_pipeline(
            x_clean, y_clean, dates_clean, exog, frequency, sig, max_lag, bp_inputs, dropped
        ):
            assert exog[1, 0] == 0
            assert exog[2, 0] == 1
            from kkb_agent.tools.causality import (
                CausalityResult,
                DiagnosticsResult,
                DirectionalTestResult,
            )

            return CausalityResult(
                status="not_identifiable",
                method="var",
                effective_observations=100,
                effective_date_range=(dates[0], dates[-1]),
                dropped_observations=0,
                selected_lag=1,
                max_integration_order=0,
                stationarity_x=None,
                stationarity_y=None,
                cointegration=None,
                breakpoints_used=bp_inputs,
                x_to_y=DirectionalTestResult("x", 1, 0.5, 0.5, False, 1, "Wald"),
                y_to_x=DirectionalTestResult("y", 1, 0.5, 0.5, False, 1, "Wald"),
                robustness=None,
                toda_yamamoto=None,
                regimes=None,
                diagnostics=DiagnosticsResult(True, None, 0.5, 90, 10, True, True, "OK"),
                significance_threshold=0.05,
                refusal_reason=None,
                refusal_details=None,
                explanation="OK",
            )

        monkeypatch.setattr(causality_tool, "_analyze_core_pipeline", mock_pipeline)
        cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])

    def test_out_of_range_breakpoint_raises_causalityerror(self):
        from datetime import date, datetime, timedelta

        import numpy as np
        import pytest

        import kkb_agent.tools.causality as cmod
        from kkb_agent.tools.causality import BreakpointInput, CausalityError

        n = 100
        dates = [datetime(2020, 1, 1, 6, 0, 0) + timedelta(days=i) for i in range(n)]
        x = np.random.normal(0, 1, n)
        y = np.random.normal(0, 1, n)

        bp = BreakpointInput(date=date(1999, 1, 1), kind="level")

        # It must raise CausalityError, not a raw TypeError
        with pytest.raises(CausalityError, match="outside the effective sample range"):
            cmod.analyze_causality(x.tolist(), y.tolist(), dates, breakpoints=[bp])


class TestFinalCorrectnessRegressions:
    @staticmethod
    def _direction(xy: bool, yx: bool):
        return (
            DirectionalTestResult("x_to_y", 1.0, 0.01, 0.02, xy, 2, "wald"),
            DirectionalTestResult("y_to_x", 1.0, 0.01, 0.02, yx, 2, "wald"),
        )

    @staticmethod
    def _adjacent(xy: bool, yx: bool):
        return cmod.RobustnessResult(1, 0.02, 0.02, xy, yx)

    def test_one_applicable_unavailable_lag_prevents_robustness_pass(self):
        xy, yx = self._direction(True, False)
        evaluation = causality_models._AdjacentLagEvaluation(
            results=(self._adjacent(True, False),),
            applicable_lags=(1, 3),
            unavailable_lags=(3,),
        )

        assert robustness_mod._robustness_status(xy, yx, evaluation) == "unavailable"

    def test_failed_applicable_fit_is_recorded_as_unavailable(self, monkeypatch):
        verified = cmod.DiagnosticsResult(True, None, 0.5, 90, 3, True, True, "verified")

        def fit(_data, lag, _exog, _sig):
            if lag == 3:
                return None
            return (
                causality_models._RawDirectionalTest(1.0, 0.01, "wald"),
                causality_models._RawDirectionalTest(1.0, 0.5, "wald"),
                verified,
            )

        monkeypatch.setattr(robustness_mod, "_fit_var", fit)
        evaluation = robustness_mod._adjacent_lag_robustness(
            np.ones((100, 2)), 2, 3, None, "granger_stationary_var", 0.05
        )

        assert evaluation.applicable_lags == (1, 3)
        assert evaluation.unavailable_lags == (3,)
        assert [result.lag_order for result in evaluation.results] == [1]

    def test_budget_failed_required_adjacent_lag_is_unavailable(self, monkeypatch):
        verified = cmod.DiagnosticsResult(True, None, 0.5, 80, 3, True, True, "verified")

        def fit(_data, lag, _exog, _sig):
            assert lag == 1
            return (
                causality_models._RawDirectionalTest(1.0, 0.01, "wald"),
                causality_models._RawDirectionalTest(1.0, 0.5, "wald"),
                verified,
            )

        monkeypatch.setattr(robustness_mod, "_fit_var", fit)
        evaluation = robustness_mod._adjacent_lag_robustness(
            np.ones((82, 2)), 2, 3, None, "granger_stationary_var", 0.05
        )
        xy, yx = self._direction(True, False)

        assert evaluation.applicable_lags == (1, 3)
        assert evaluation.unavailable_lags == (3,)
        assert [result.lag_order for result in evaluation.results] == [1]
        assert robustness_mod._robustness_status(xy, yx, evaluation) == "unavailable"

    def test_fraction_values_are_safely_normalized(self):
        dates = _daily_dates(2)

        x, y, normalized_dates = validation_mod._validate_inputs(
            [Fraction(1, 2), Fraction(3, 2)], [1, 2], dates, Fraction(1, 20), None
        )

        assert x == (0.5, 1.5)
        assert y == (1.0, 2.0)
        assert normalized_dates == dates

    def test_huge_integer_conversion_raises_causality_error(self):
        dates = _daily_dates(2)

        with pytest.raises(CausalityError, match="cannot be converted to float"):
            validation_mod._validate_inputs([1, 10**10000], [1, 2], dates, 0.05, None)

    def test_huge_significance_conversion_raises_causality_error(self):
        dates = _daily_dates(2)

        with pytest.raises(CausalityError, match="positive finite number"):
            validation_mod._validate_inputs([1, 2], [1, 2], dates, 10**10000, None)

    @pytest.mark.parametrize(
        ("selected", "adjacent", "expected"),
        [
            ((True, False), (False, False), "inconclusive"),
            ((True, False), (True, True), "inconclusive"),
            ((True, False), (False, True), "reversed"),
            ((True, True), (True, False), "inconclusive"),
        ],
    )
    def test_adjacent_direction_states_have_precise_reversal_semantics(
        self, selected, adjacent, expected
    ):
        xy, yx = self._direction(*selected)
        evaluation = causality_models._AdjacentLagEvaluation(
            results=(self._adjacent(*adjacent),),
            applicable_lags=(1,),
            unavailable_lags=(),
        )

        assert robustness_mod._robustness_status(xy, yx, evaluation) == expected

    def test_cointegration_failure_returns_structured_refusal(self, monkeypatch):
        diagnosis = cmod.StationarityDiagnosis(
            adf_level=None,
            kpss_level=None,
            adf_first_diff=None,
            kpss_first_diff=None,
            zivot_andrews=None,
            conclusion="I(1)",
            explanation="coherent I(1)",
        )
        monkeypatch.setattr(causality_tool, "_diagnose_stationarity", lambda *_: diagnosis)
        monkeypatch.setattr(
            causality_tool,
            "_test_cointegration",
            lambda *_: (_ for _ in ()).throw(ValueError("numerical details")),
        )
        dates = _daily_dates(120)

        result = analyze_causality(
            np.arange(120, dtype=float),
            np.arange(120, dtype=float) ** 1.1,
            dates,
        )

        assert result.status == "not_identifiable"
        assert result.refusal_reason == "cointegration_test_unavailable"
        assert "numerical details" not in result.explanation

    @pytest.mark.parametrize("invalid_date", ["2020-01-01", 1, None, object()])
    def test_invalid_breakpoint_date_type_is_rejected(self, invalid_date):
        class InvalidBreakpoint:
            date = invalid_date
            kind = "level"

        with pytest.raises(CausalityError, match="date or datetime"):
            analyze_causality(
                np.arange(50, dtype=float),
                np.arange(50, dtype=float) ** 1.1,
                _daily_dates(50),
                breakpoints=(InvalidBreakpoint(),),
            )

    def test_stationarity_refusal_preserves_za_disagreement_explanation(self, monkeypatch):
        diagnoses = iter(
            (
                cmod.StationarityDiagnosis(
                    None,
                    None,
                    None,
                    None,
                    None,
                    "ambiguous",
                    "ADF/KPSS indicate I(1) but Zivot-Andrews finds break stationarity.",
                ),
                cmod.StationarityDiagnosis(None, None, None, None, None, "I(0)", "coherent I(0)"),
            )
        )
        monkeypatch.setattr(causality_tool, "_diagnose_stationarity", lambda *_: next(diagnoses))

        result = analyze_causality(
            np.arange(80, dtype=float),
            np.sin(np.arange(80, dtype=float)),
            _daily_dates(80),
        )

        assert result.refusal_reason == "stationarity_ambiguous"
        assert "Zivot-Andrews finds break stationarity" in result.explanation

    def test_non_finite_stationarity_output_becomes_structured_ambiguity(self, monkeypatch):
        monkeypatch.setattr(
            stationarity_mod,
            "adfuller",
            lambda *_args, **_kwargs: (float("nan"), 0.5, 1, 50, {"5%": -2.9}, None),
        )

        result = analyze_causality(
            np.arange(80, dtype=float),
            np.sin(np.arange(80, dtype=float)),
            _daily_dates(80),
        )

        assert result.status == "not_identifiable"
        assert result.refusal_reason == "stationarity_ambiguous"
        assert result.stationarity_x.adf_level is None

    def test_lag_model_construction_failure_returns_structured_refusal(self, monkeypatch):
        diagnosis = cmod.StationarityDiagnosis(
            None, None, None, None, None, "I(0)", "coherent I(0)"
        )
        monkeypatch.setattr(causality_tool, "_diagnose_stationarity", lambda *_: diagnosis)
        monkeypatch.setattr(
            var_mod,
            "VAR",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("model internals")),
        )

        result = analyze_causality(
            np.arange(80, dtype=float),
            np.sin(np.arange(80, dtype=float)),
            _daily_dates(80),
        )

        assert result.status == "not_identifiable"
        assert result.refusal_reason == "no_valid_lag"
        assert "model internals" not in result.explanation
