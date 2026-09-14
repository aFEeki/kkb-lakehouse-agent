"""Injectable execution boundary behind the HTTP/SSE transport."""

from collections.abc import AsyncIterator
from typing import Protocol

from kkb_agent.api.contracts import AskRequest, StreamEvent


class AskRunner(Protocol):
    """Produce an ordered typed event stream for one validated ask request."""

    def run(self, request: AskRequest) -> AsyncIterator[StreamEvent]: ...


class UnavailableAskRunner:
    """Production placeholder until the orchestration task supplies a runner."""

    async def run(self, request: AskRequest) -> AsyncIterator[StreamEvent]:
        del request
        if False:  # pragma: no cover - makes this an async iterator without fake output
            yield
        raise RuntimeError("Ask orchestration is not configured")
