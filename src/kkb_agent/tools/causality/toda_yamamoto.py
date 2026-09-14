"""Toda-Yamamoto augmented-VAR robustness analysis."""

from __future__ import annotations

from math import isfinite

import numpy as np
from scipy import stats as scipy_stats

from .models import TodaYamamotoResult


def _run_toda_yamamoto(
    levels_data: np.ndarray,
    p: int,
    d_max: int,
    exog: np.ndarray | None,
) -> TodaYamamotoResult | None:
    """Toda-Yamamoto augmented VAR: Wald test on first p lags only."""
    p_aug = p + d_max
    T = len(levels_data)
    T_eff = T - p_aug
    K = levels_data.shape[1]  # 2

    if T_eff < 2 * (1 + K * p_aug):
        return None

    # Build design matrix: [const, x_L1, y_L1, ..., x_Lp_aug, y_Lp_aug, exog...]
    Z = np.ones((T_eff, 1))
    for j in range(1, p_aug + 1):
        Z = np.column_stack([Z, levels_data[p_aug - j : T - j]])
    if exog is not None:
        Z = np.column_stack([Z, exog[p_aug:]])

    Y = levels_data[p_aug:]

    try:
        ZtZ_inv = np.linalg.inv(Z.T @ Z)
    except np.linalg.LinAlgError:
        return None

    B = ZtZ_inv @ (Z.T @ Y)
    residuals = Y - Z @ B

    def wald_test(eq_idx: int, restriction_indices: list[int]):
        beta = B[:, eq_idx]
        sigma2 = float(residuals[:, eq_idx] @ residuals[:, eq_idx]) / (T_eff - Z.shape[1])
        cov_beta = sigma2 * ZtZ_inv
        q = len(restriction_indices)
        R = np.zeros((q, Z.shape[1]))
        for i, idx in enumerate(restriction_indices):
            R[i, idx] = 1.0
        Rb = R @ beta
        middle = R @ cov_beta @ R.T
        try:
            stat = float(Rb @ np.linalg.solve(middle, Rb))
        except np.linalg.LinAlgError:
            return None
        pval = float(scipy_stats.chi2.sf(stat, q))
        if not isfinite(stat) or not isfinite(pval):
            return None
        return stat, pval

    # x→y: first p lags of x in y equation.  x at lag j is col 1+(j-1)*K.
    x_in_y = [1 + (j - 1) * K for j in range(1, p + 1)]
    # y→x: first p lags of y in x equation.  y at lag j is col 1+(j-1)*K+1.
    y_in_x = [1 + (j - 1) * K + 1 for j in range(1, p + 1)]

    xy = wald_test(1, x_in_y)
    yx = wald_test(0, y_in_x)
    if xy is None or yx is None:
        return None

    return TodaYamamotoResult(
        augmented_lag_order=p_aug,
        d_max=d_max,
        effective_lags_tested=p,
        x_to_y_statistic=xy[0],
        x_to_y_p_value=xy[1],
        y_to_x_statistic=yx[0],
        y_to_x_p_value=yx[1],
        explanation=(
            f"Toda-Yamamoto VAR({p_aug}) with Wald restrictions on first {p} lags only "
            f"(d_max={d_max}). This is a robustness check and cannot establish the "
            f"primary conclusion by itself."
        ),
    )
