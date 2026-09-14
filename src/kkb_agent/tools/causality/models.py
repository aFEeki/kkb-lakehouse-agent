"""Immutable data contracts for causality analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

CausalityStatus = Literal[
    "predictive_relationship_supported",
    "limited_evidence",
    "not_identifiable",
]

CausalMethod = Literal[
    "granger_stationary_var",
    "granger_differenced_var",
    "vecm",
]

IntegrationOrder = Literal["I(0)", "I(1)", "ambiguous"]

RefusalReason = Literal[
    "insufficient_observations",
    "constant_series",
    "stationarity_ambiguous",
    "mixed_integration_order",
    "unsupported_integration_order",
    "cointegration_requires_unsupported_model",
    "cointegration_test_unavailable",
    "no_valid_lag",
    "unstable_model",
    "structural_break_confounding",
    "residual_diagnostics_failed",
    "lag_sensitivity_reversal",
    "causality_not_identifiable",
    "insufficient_degrees_of_freedom",
    "degenerate_series",
    "non_contiguous_after_missing_removal",
    "irregular_frequency",
    "regime_robustness_failed",
]

# ---------------------------------------------------------------------------
# Data contracts  (frozen dataclasses — no Pydantic / no statsmodels objects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StationarityTestResult:
    """Single ADF or KPSS test outcome."""

    test_name: str
    statistic: float
    p_value: float | None
    critical_values: dict[str, float]
    regression: str
    lags_or_bandwidth: int
    interpretation: str


@dataclass(frozen=True)
class ZivotAndrewsResult:
    """Zivot-Andrews one-break sensitivity test."""

    statistic: float
    p_value: float
    critical_values: dict[str, float]
    estimated_break_date: date | datetime
    regression: str
    lags_used: int
    interpretation: str


@dataclass(frozen=True)
class StationarityDiagnosis:
    """Paired ADF and KPSS conclusions."""

    adf_level: StationarityTestResult | None
    kpss_level: StationarityTestResult | None
    adf_first_diff: StationarityTestResult | None
    kpss_first_diff: StationarityTestResult | None
    zivot_andrews: ZivotAndrewsResult | None
    conclusion: IntegrationOrder
    explanation: str
    za_unverified: bool = False


@dataclass(frozen=True)
class CointegrationResult:
    """Engle-Granger bivariate cointegration test."""

    method: str
    statistic: float
    p_value: float
    critical_values: dict[str, float]
    cointegrated: bool
    explanation: str


@dataclass(frozen=True)
class DirectionalTestResult:
    """Granger-predictive test for one direction, with Holm-corrected p-value."""

    direction: str
    statistic: float
    p_value: float
    corrected_p_value: float
    significant: bool
    lags_tested: int
    test_type: str


@dataclass(frozen=True)
class RobustnessResult:
    """Directional test re-run at an adjacent lag order."""

    lag_order: int
    x_to_y_p_value: float
    y_to_x_p_value: float
    x_to_y_significant: bool
    y_to_x_significant: bool


@dataclass(frozen=True)
class TodaYamamotoResult:
    """Toda-Yamamoto augmented VAR robustness check (never primary evidence)."""

    augmented_lag_order: int
    d_max: int
    effective_lags_tested: int
    x_to_y_statistic: float
    x_to_y_p_value: float
    y_to_x_statistic: float
    y_to_x_p_value: float
    explanation: str


@dataclass(frozen=True)
class VecmStabilityEvidence:
    """Explicit evidence of VECM companion-matrix stability.

    The numerical tolerance protects against floating-point modulus deviations
    from mathematically exact unit roots, preserving the expected count (K - r)
    where K is the number of variables and r is the cointegration rank.
    """

    expected_unit_roots: int
    observed_unit_roots: int | None
    tolerance: float
    largest_non_unit_root_modulus: float | None
    verified: bool
    explanation: str


@dataclass(frozen=True)
class DiagnosticsResult:
    """Model diagnostic summary."""

    model_stable: bool | None
    vecm_stability: VecmStabilityEvidence | None
    residual_autocorrelation_p_value: float | None
    degrees_of_freedom: int
    parameter_count: int
    passes: bool
    verified: bool
    explanation: str


@dataclass(frozen=True)
class BreakpointInput:
    """Breakpoint accepted from change-detection output or user specification."""

    date: date | datetime
    kind: str  # "level" or "trend" — compatible with ChangeKind


@dataclass(frozen=True)
class RegimeResult:
    """Independent regime-split robustness evaluation."""

    regime_index: int
    date_range: tuple[date | datetime, date | datetime]
    effective_observations: int
    status: Literal["passed", "reversed", "inconclusive", "unavailable"]
    method: CausalMethod | None
    selected_lag: int | None
    x_to_y: DirectionalTestResult | None
    y_to_x: DirectionalTestResult | None
    diagnostics: DiagnosticsResult | None
    reason: str | None


@dataclass(frozen=True)
class CausalityResult:
    """Complete, serializable result contract for causality analysis."""

    status: CausalityStatus
    method: CausalMethod | None
    effective_observations: int
    effective_date_range: tuple[date | datetime, date | datetime] | None
    dropped_observations: int
    selected_lag: int | None
    max_integration_order: int
    stationarity_x: StationarityDiagnosis | None
    stationarity_y: StationarityDiagnosis | None
    cointegration: CointegrationResult | None
    breakpoints_used: tuple[BreakpointInput, ...]
    x_to_y: DirectionalTestResult | None
    y_to_x: DirectionalTestResult | None
    robustness: tuple[RobustnessResult, ...] | None
    toda_yamamoto: TodaYamamotoResult | None
    regimes: tuple[RegimeResult, ...] | None
    diagnostics: DiagnosticsResult | None
    significance_threshold: float
    refusal_reason: RefusalReason | None
    refusal_details: str | None
    explanation: str


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class CausalityError(ValueError):
    """Raised for input validation failures that prevent analysis."""


@dataclass(frozen=True)
class _RawDirectionalTest:
    statistic: float
    p_value: float
    test_type: str


@dataclass(frozen=True)
class _AdjacentLagEvaluation:
    results: tuple[RobustnessResult, ...]
    applicable_lags: tuple[int, ...]
    unavailable_lags: tuple[int, ...]
