"""Final evidence classification policy."""

from __future__ import annotations

from typing import Literal

from .models import CausalityStatus, DirectionalTestResult, RefusalReason


def _classify(
    xy: DirectionalTestResult,
    yx: DirectionalTestResult,
    robustness_status: Literal["passed", "reversed", "inconclusive", "unavailable"],
    coint_not_tested: bool,
    diag_unverified: bool,
    sensitivity_unverified: bool,
) -> tuple[CausalityStatus, RefusalReason | None, str]:
    xy_sig = xy.significant
    yx_sig = yx.significant

    if not xy_sig and not yx_sig:
        return (
            "not_identifiable",
            "causality_not_identifiable",
            "Evidence is insufficient: no Granger-predictive relationship "
            "detected in either direction under this specification.",
        )

    # Determine if result must be capped at limited_evidence
    capped = (
        robustness_status != "passed"
        or coint_not_tested
        or diag_unverified
        or sensitivity_unverified
    )
    direction_label = "bidirectional" if xy_sig and yx_sig else "X → Y" if xy_sig else "Y → X"
    detail = (
        "X has predictive information for Y"
        if xy_sig and not yx_sig
        else "Y has predictive information for X"
        if yx_sig and not xy_sig
        else "X and Y have mutual predictive information"
    )

    if capped:
        reasons: list[str] = []
        if robustness_status == "reversed":
            reasons.append("direction reverses at an adjacent lag order")
        elif robustness_status == "inconclusive":
            reasons.append("direction is inconsistent at an adjacent lag order")
        elif robustness_status == "unavailable":
            reasons.append("adjacent-lag robustness unavailable")
        if coint_not_tested:
            reasons.append("cointegration not tested (insufficient sample)")
        if diag_unverified:
            reasons.append("required diagnostics unverified (deferred)")
        if sensitivity_unverified:
            reasons.append("Zivot-Andrews structural-break sensitivity check unavailable")
        return (
            "limited_evidence",
            None,
            f"Limited Granger-predictive evidence ({direction_label}). "
            f"{detail} under this specification. "
            f"Capped at limited_evidence: {'; '.join(reasons)}. "
            f"This does not establish structural causation.",
        )

    return (
        "predictive_relationship_supported",
        None,
        f"Granger-predictive evidence supported ({direction_label}). "
        f"{detail} under this specification. "
        f"This is predictive/Granger causality conditional on the included "
        f"variables and does not establish structural causation.",
    )
