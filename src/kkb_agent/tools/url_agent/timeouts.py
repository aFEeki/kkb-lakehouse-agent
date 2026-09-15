"""One clock for a whole URL agent turn, so no single source can stall it."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from math import isfinite

import httpx

from kkb_agent.tools.url_agent.models import URLAgentError
from kkb_agent.tools.url_safety import SafeURLFetcher, URLSafetyLimits

# A turn that takes longer than this has already lost the demo, whatever it returns.
DEFAULT_TOTAL_SECONDS = 60.0
DEFAULT_FETCH_SECONDS = 15.0


class ToolTimeoutError(URLAgentError):
    """A URL agent operation ran out of its time budget.

    `partial` carries whatever was already read, so a caller can present an incomplete
    answer with that stated rather than throwing the work away.
    """

    def __init__(self, message: str, *, partial: object | None = None):
        super().__init__(message)
        self.partial = partial


@dataclass
class Deadline:
    """A wall clock for one turn. The clock is injectable so tests need no sleeping."""

    total_seconds: float
    clock: Callable[[], float] = time.monotonic
    _started: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.total_seconds, (int, float)) or isinstance(self.total_seconds, bool):
            raise ValueError("total_seconds must be a positive finite number")
        self.total_seconds = float(self.total_seconds)
        if not isfinite(self.total_seconds) or self.total_seconds <= 0:
            raise ValueError("total_seconds must be a positive finite number")
        self._started = self.clock()

    @property
    def elapsed(self) -> float:
        return self.clock() - self._started

    @property
    def remaining(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0

    def require(self, operation: str, *, partial: object | None = None) -> float:
        """Return the seconds left, or refuse to start `operation` and say so."""

        remaining = self.remaining
        if remaining <= 0:
            raise ToolTimeoutError(
                f"{operation} was not started: the {self.total_seconds:g}s budget for this "
                f"turn was already spent after {self.elapsed:.1f}s",
                partial=partial,
            )
        return remaining

    def bounded(self, ceiling_seconds: float) -> float:
        """The smaller of what a step normally gets and what the turn has left."""

        return min(float(ceiling_seconds), self.remaining)


@contextmanager
def bounded_fetcher(
    *,
    per_request_seconds: float = DEFAULT_FETCH_SECONDS,
    limits: URLSafetyLimits | None = None,
) -> Iterator[SafeURLFetcher]:
    """A `SafeURLFetcher` whose every request carries an explicit timeout.

    `SafeURLFetcher` builds its own client at 30s when none is supplied, and a turn that
    can afford 60s in total cannot afford two of those. The client is injected rather than
    the safety module changed -- and because an injected client is not the fetcher's to
    close, this is a context manager that owns it.
    """

    if (
        isinstance(per_request_seconds, bool)
        or not isinstance(per_request_seconds, (int, float))
        or not isfinite(per_request_seconds)
        or per_request_seconds <= 0
    ):
        raise ValueError("per_request_seconds must be a positive finite number")

    with httpx.Client(
        follow_redirects=False, timeout=httpx.Timeout(float(per_request_seconds))
    ) as client:
        with SafeURLFetcher(limits=limits, client=client) as fetcher:
            yield fetcher
