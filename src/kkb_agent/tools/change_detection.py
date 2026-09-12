"""Deterministic level and trend change detection with PELT."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from numbers import Real
from typing import Literal

import numpy as np
import ruptures as rpt

ChangeKind = Literal["level", "trend"]


class ChangeDetectionError(ValueError):
    """Raised when a series cannot be analyzed safely."""


@dataclass(frozen=True)
class ChangeDetectionParameters:
    method: str
    model: str
    level_penalty: float
    trend_penalty: float
    minimum_segment_length: int
    jump: int
    trend_transform: str


@dataclass(frozen=True)
class Breakpoint:
    """The first observation in a newly detected regime."""

    index: int
    date: date | datetime
    kind: ChangeKind


@dataclass(frozen=True)
class ChangeDetectionResult:
    breakpoints: tuple[Breakpoint, ...]
    parameters: ChangeDetectionParameters
    observation_count: int

    def for_kind(self, kind: ChangeKind) -> tuple[Breakpoint, ...]:
        """Return one typed breakpoint stream for a downstream consumer."""

        return tuple(point for point in self.breakpoints if point.kind == kind)


def detect_changes(
    values: Sequence[Real],
    dates: Sequence[date | datetime],
    *,
    level_penalty: Real,
    trend_penalty: Real,
    minimum_segment_length: int,
    jump: int = 1,
) -> ChangeDetectionResult:
    """Detect level and first-difference trend regime changes with PELT."""

    signal, normalized_dates = _validate_inputs(
        values,
        dates,
        level_penalty=level_penalty,
        trend_penalty=trend_penalty,
        minimum_segment_length=minimum_segment_length,
        jump=jump,
    )
    level_penalty = float(level_penalty)
    trend_penalty = float(trend_penalty)

    level_boundaries = _pelt_boundaries(
        signal,
        penalty=level_penalty,
        minimum_segment_length=minimum_segment_length,
        jump=jump,
    )
    trend_boundaries = _pelt_boundaries(
        np.diff(signal),
        penalty=trend_penalty,
        minimum_segment_length=minimum_segment_length,
        jump=jump,
    )

    breakpoints = [
        Breakpoint(index=index, date=normalized_dates[index], kind="level")
        for index in level_boundaries
    ]
    # diff[i] is the change from values[i] to values[i + 1]. A boundary at
    # diff index i therefore starts at original observation i + 1.
    breakpoints.extend(
        Breakpoint(index=index + 1, date=normalized_dates[index + 1], kind="trend")
        for index in trend_boundaries
    )
    breakpoints.sort(key=lambda point: (point.index, point.kind))

    return ChangeDetectionResult(
        breakpoints=tuple(breakpoints),
        parameters=ChangeDetectionParameters(
            method="PELT",
            model="l2",
            level_penalty=level_penalty,
            trend_penalty=trend_penalty,
            minimum_segment_length=minimum_segment_length,
            jump=jump,
            trend_transform="first_difference",
        ),
        observation_count=len(signal),
    )


def _pelt_boundaries(
    signal: np.ndarray,
    *,
    penalty: float,
    minimum_segment_length: int,
    jump: int,
) -> tuple[int, ...]:
    result = rpt.Pelt(model="l2", min_size=minimum_segment_length, jump=jump).fit(
        signal.reshape(-1, 1)
    )
    # ruptures includes the signal length as the terminal segment boundary.
    return tuple(result.predict(pen=penalty)[:-1])


def _validate_inputs(
    values,
    dates,
    *,
    level_penalty,
    trend_penalty,
    minimum_segment_length,
    jump,
):
    if len(values) != len(dates):
        raise ChangeDetectionError("values and dates must have the same length")
    if isinstance(minimum_segment_length, bool) or not isinstance(minimum_segment_length, int):
        raise ChangeDetectionError("minimum_segment_length must be an integer")
    if minimum_segment_length < 2:
        raise ChangeDetectionError("minimum_segment_length must be at least 2")
    if len(values) - 1 < 2 * minimum_segment_length:
        raise ChangeDetectionError(
            "The first-difference signal must contain at least two complete segments"
        )
    if isinstance(jump, bool) or not isinstance(jump, int) or jump < 1:
        raise ChangeDetectionError("jump must be a positive integer")

    for name, penalty in (
        ("level_penalty", level_penalty),
        ("trend_penalty", trend_penalty),
    ):
        if isinstance(penalty, bool) or not isinstance(penalty, Real):
            raise ChangeDetectionError(f"{name} must be a positive finite number")
        if not isfinite(float(penalty)) or float(penalty) <= 0:
            raise ChangeDetectionError(f"{name} must be a positive finite number")

    normalized_dates = tuple(dates)
    if any(not isinstance(item, (date, datetime)) for item in normalized_dates):
        raise ChangeDetectionError("dates must contain only date or datetime values")
    try:
        increasing = all(
            current > previous
            for previous, current in zip(normalized_dates, normalized_dates[1:], strict=False)
        )
    except TypeError as exc:
        raise ChangeDetectionError(
            "dates must be mutually comparable and strictly increasing"
        ) from exc
    if not increasing:
        raise ChangeDetectionError("dates must be strictly increasing without duplicates")

    normalized_values: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ChangeDetectionError("values must contain only finite numeric observations")
        converted = float(value)
        if not isfinite(converted):
            raise ChangeDetectionError("values must contain only finite numeric observations")
        normalized_values.append(converted)
    return np.asarray(normalized_values, dtype=float), normalized_dates
