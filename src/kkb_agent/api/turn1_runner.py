"""SCRUM-69/44 - stream turn 1 over the ask contract.

The transport, the event contract and the five stage names were already there; turn 1 was
a function nothing called. This connects them, and the stage events report real phase
boundaries rather than decoration - `build_turn1` calls back as it crosses them, so a
stage that says "succeeded" did.

Turn 1 is synchronous and CPU-bound: it opens DuckDB, resolves against 872 concepts, and
may call MIA. Running it inline would block the event loop and the stream would arrive in
one burst at the end, which defeats the point of streaming a pipeline. It runs in a worker
thread and pushes stage events back through a queue as it crosses them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from kkb_agent.agent.turn1 import TurnResult, build_turn1
from kkb_agent.api.contracts import (
    AskRequest,
    CompletionEvent,
    CompletionPayload,
    ErrorEvent,
    ErrorPayload,
    ResultEvent,
    ResultPayload,
    StageEndEvent,
    StageEndPayload,
    StageStartEvent,
    StageStartPayload,
    StreamEvent,
    ToolSelectedEvent,
    ToolSelectedPayload,
)

logger = logging.getLogger(__name__)

_FAILED = "Analiz tamamlanamadı. Lütfen tekrar deneyin."
_SENTINEL = object()


class TurnOneAskRunner:
    """Runs turn 1 for any question and streams its stages.

    Turn 1 answers one published question. A request asking something else still gets that
    analysis, and the answer says which question was actually answered - silently
    returning housing-loan findings for a question about deposits would be worse than
    either refusing or disclosing.
    """

    def __init__(self, catalog: Path | str, *, planner=None):
        self._catalog = catalog
        self._planner = planner

    async def run(self, request: AskRequest) -> AsyncIterator[StreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sequence = 0

        def on_stage(stage, event, *, outcome=None, tool=None):
            loop.call_soon_threadsafe(queue.put_nowait, (stage, event, outcome, tool))

        async def execute() -> TurnResult | BaseException:
            try:
                return await asyncio.to_thread(
                    build_turn1, self._catalog, planner=self._planner, on_stage=on_stage
                )
            except BaseException as exc:  # surfaced as an error event, never as a 500
                logger.exception("turn 1 failed for analysis %s", request.analysis_id)
                return exc
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

        worker = asyncio.create_task(execute())

        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            stage, event, outcome, tool = item
            if event == "start":
                yield _stage_start(request, sequence, stage)
                sequence += 1
                if tool:
                    yield _tool_selected(request, sequence, tool)
                    sequence += 1
            else:
                yield _stage_end(request, sequence, stage, outcome or "succeeded")
                sequence += 1

        outcome_or_error = await worker
        if isinstance(outcome_or_error, BaseException):
            yield _error(request, sequence)
            sequence += 1
            yield _completion(request, sequence, "failed", request.version)
            return

        result = outcome_or_error
        yield _result(request, sequence, result)
        sequence += 1
        yield _completion(request, sequence, "succeeded", result.frame.version)


def answer_text(result: TurnResult, question: str) -> str:
    """The findings as prose, with what had to be disclosed, in Turkish.

    Built from the frame's findings rather than generated, so the answer cannot contain a
    number the analysis did not compute. If the question was not the one turn 1 answers,
    that is said first rather than left for the reader to notice.
    """
    lines: list[str] = []
    if question.strip().casefold() != _question().strip().casefold():
        lines.append(
            f"Not: bu sürüm yalnızca şu soruyu yanıtlıyor — “{_question()}”. "
            "Sorduğunuz soru için henüz bir analiz üretilmiyor."
        )
    lines += [finding.statement for finding in result.frame.findings]
    for finding in result.frame.findings:
        lines += [f"  - {caveat}" for caveat in finding.caveats]
    if result.caveats:
        lines.append("Açıklanması gerekenler:")
        lines += [f"  ! {caveat}" for caveat in result.caveats]
    lines.append(
        f"Plan: {result.planned_by}" + (f" ({', '.join(result.plan)})" if result.plan else "")
    )
    return "\n".join(lines)


def _question() -> str:
    from kkb_agent.agent.turn1 import QUESTION

    return QUESTION


def _envelope(request: AskRequest, sequence: int, version: int | None = None) -> dict:
    return {
        "event_id": f"turn1-{request.analysis_id}-{sequence}",
        "analysis_id": request.analysis_id,
        "sequence": sequence,
        "frame_version": request.version if version is None else version,
        "occurred_at": datetime.now(UTC),
    }


def _stage_start(request: AskRequest, sequence: int, stage: str) -> StageStartEvent:
    return StageStartEvent(
        **_envelope(request, sequence), type="stage_start", payload=StageStartPayload(stage=stage)
    )


def _stage_end(request: AskRequest, sequence: int, stage: str, outcome: str) -> StageEndEvent:
    return StageEndEvent(
        **_envelope(request, sequence),
        type="stage_end",
        payload=StageEndPayload(stage=stage, outcome=outcome),
    )


def _tool_selected(request: AskRequest, sequence: int, tool: str) -> ToolSelectedEvent:
    return ToolSelectedEvent(
        **_envelope(request, sequence),
        type="tool_selected",
        payload=ToolSelectedPayload(tool=tool),
    )


def _result(request: AskRequest, sequence: int, result: TurnResult) -> ResultEvent:
    return ResultEvent(
        **_envelope(request, sequence, result.frame.version),
        type="result",
        payload=ResultPayload(frame=result.frame, answer=answer_text(result, request.question)),
    )


def _error(request: AskRequest, sequence: int) -> ErrorEvent:
    return ErrorEvent(
        **_envelope(request, sequence),
        type="error",
        payload=ErrorPayload(code="TURN_ONE_FAILED", user_message=_FAILED, retryable=True),
    )


def _completion(request: AskRequest, sequence: int, outcome: str, version: int) -> CompletionEvent:
    return CompletionEvent(
        **_envelope(request, sequence, version),
        type="completion",
        payload=CompletionPayload(outcome=outcome),
    )
