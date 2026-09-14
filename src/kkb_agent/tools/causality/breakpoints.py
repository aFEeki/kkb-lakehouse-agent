"""Breakpoint normalization, dummy construction, and regime boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

import numpy as np

from .models import BreakpointInput, CausalityError


def _calendar_date(value):
    return value.date() if isinstance(value, datetime) else value


def _normalize_breakpoints(
    breakpoints: Sequence,
    dates_clean: tuple,
) -> tuple[BreakpointInput, ...]:
    result: list[BreakpointInput] = []
    for bp in breakpoints:
        if hasattr(bp, "date") and hasattr(bp, "kind"):
            if not isinstance(bp.date, (date, datetime)):
                raise CausalityError("Breakpoint date must be a date or datetime")
            raw_kind = bp.kind.value if hasattr(bp.kind, "value") else bp.kind
            kind = str(raw_kind)
            if kind not in ("level", "trend"):
                raise CausalityError("Breakpoint kind must be 'level' or 'trend'")

            if isinstance(bp, BreakpointInput):
                # Only reconstruct if necessary to preserve original reference if already correct
                if bp.kind != kind:
                    result.append(BreakpointInput(date=bp.date, kind=kind))
                else:
                    result.append(bp)
            else:
                result.append(BreakpointInput(date=bp.date, kind=kind))
        else:
            raise CausalityError(
                "breakpoints must be BreakpointInput instances or have 'date' and 'kind' attributes"
            )
    # Validate dates are within range
    if dates_clean:
        d_start = _calendar_date(dates_clean[0])
        d_end = _calendar_date(dates_clean[-1])
        for bp in result:
            bp_cal = _calendar_date(bp.date)
            if bp_cal < d_start or bp_cal > d_end:
                raise CausalityError(
                    f"Breakpoint date {bp.date} is outside the effective sample range "
                    f"[{dates_clean[0]}, {dates_clean[-1]}]"
                )
    return tuple(result)


def _build_break_dummies(
    bp_inputs: tuple[BreakpointInput, ...],
    dates: tuple,
) -> np.ndarray | None:
    if not bp_inputs:
        return None
    n = len(dates)
    dummies: list[np.ndarray] = []
    for bp in bp_inputs:
        bp_cal = _calendar_date(bp.date)
        dummy = np.zeros(n, dtype=float)
        for i, d in enumerate(dates):
            if _calendar_date(d) >= bp_cal:
                if bp.kind == "level":
                    dummy[i:] = 1.0
                else:
                    # trend break (slope change)
                    for j in range(i, n):
                        dummy[j] = float(j - i)
                break
        dummies.append(dummy)
    return np.column_stack(dummies)


def _regimes_too_small(
    bp_inputs: tuple[BreakpointInput, ...],
    dates: tuple,
    min_regime: int,
) -> bool:
    bp_positions: list[int] = []
    for bp in bp_inputs:
        bp_cal = _calendar_date(bp.date)
        for i, d in enumerate(dates):
            if _calendar_date(d) >= bp_cal:
                bp_positions.append(i)
                break
    bp_positions = sorted(set(bp_positions))
    boundaries = [0, *bp_positions, len(dates)]
    return any(boundaries[i + 1] - boundaries[i] < min_regime for i in range(len(boundaries) - 1))
