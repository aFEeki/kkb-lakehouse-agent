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
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from kkb_agent.agent.router import run_tool
from kkb_agent.agent.turn1 import TurnResult, build_turn1
from kkb_agent.agent.turn2 import TurnTwoResult, build_turn2
from kkb_agent.agent.turn3 import HPI_KEY, TurnThreeResult, build_turn3
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
from kkb_agent.api.frame_store import AnalysisFrameStore, AnalysisFrameStoreError
from kkb_agent.catalog.series_source import CatalogSeriesSource

logger = logging.getLogger(__name__)

_FAILED = "Analiz tamamlanamadı. Lütfen tekrar deneyin."
_SENTINEL = object()


def classify_published_turn(question: str) -> int | None:
    """Recognize only the three bounded Turkish demo intents."""
    normalized = re.sub(r"\s+", " ", question.strip().casefold())
    if any(
        term in normalized for term in ("konut fiyat endeksi", "fiyat endeksini", "kfe")
    ) and any(term in normalized for term in ("ekle", "sütun", "nedeni", "olabilir")):
        return 3
    if "kredi" in normalized and any(
        term in normalized for term in ("enflasyondan arındır", "reel", "sabit fiyat", "deflat")
    ):
        return 2
    if "konut" in normalized and "kredi" in normalized and "faiz" in normalized:
        return 1
    return None


class TurnOneAskRunner:
    """Runs turn 1 for any question and streams its stages.

    Turn 1 answers one published question. A request asking something else still gets that
    analysis, and the answer says which question was actually answered - silently
    returning housing-loan findings for a question about deposits would be worse than
    either refusing or disclosing.
    """

    def __init__(self, catalog: Path | str, *, planner=None, frame_store=None):
        self._catalog = catalog
        self._planner = planner
        self._frame_store = frame_store or AnalysisFrameStore()

    async def run(self, request: AskRequest) -> AsyncIterator[StreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        sequence = 0
        requested_turn = classify_published_turn(request.question)

        previous = None
        if request.version != 0:
            try:
                previous = self._frame_store.get_current(request.analysis_id, request.version)
            except AnalysisFrameStoreError:
                yield _error(
                    request,
                    sequence,
                    code="ANALYSIS_VERSION_NOT_FOUND",
                    message="İstenen analiz sürümü mevcut veya güncel değil.",
                    retryable=False,
                )
                yield _completion(request, sequence + 1, "failed", request.version)
                return

        expected_turn = (
            1
            if previous is None
            else 3
            if any("deflated_by" in column.key for column in previous.columns)
            else 2
        )
        if requested_turn is None:
            # Not a published turn: hand it to the tool router rather than refusing.
            async for event in self._route(request, sequence):
                yield event
            return

        if requested_turn != expected_turn:
            # A real turn, asked out of order. Still a sequencing error, not a tool question.
            yield _error(
                request,
                sequence,
                code="UNSUPPORTED_PUBLISHED_TURN",
                message="Bu soru mevcut analiz sürümü için desteklenen yayınlanmış tur değil.",
                retryable=False,
            )
            yield _completion(request, sequence + 1, "failed", request.version)
            return

        def on_stage(stage, event, *, outcome=None, tool=None):
            loop.call_soon_threadsafe(queue.put_nowait, (stage, event, outcome, tool))

        async def execute() -> TurnResult | TurnTwoResult | TurnThreeResult | BaseException:
            try:
                if previous is not None:
                    source = CatalogSeriesSource(self._catalog)
                    try:
                        if any(column.key == HPI_KEY for column in previous.columns):
                            raise ValueError("No published turn follows Turn 3")
                        if any("deflated_by" in column.key for column in previous.columns):
                            return await asyncio.to_thread(
                                build_turn3, previous, source, on_stage=on_stage
                            )
                        return await asyncio.to_thread(
                            build_turn2, previous, source, on_stage=on_stage
                        )
                    finally:
                        source.close()
                return await asyncio.to_thread(
                    build_turn1,
                    self._catalog,
                    frame_id=request.analysis_id,
                    planner=self._planner,
                    on_stage=on_stage,
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
            yield _error(
                request,
                sequence,
                code=(
                    "TURN_THREE_FAILED"
                    if previous is not None
                    and any("deflated_by" in column.key for column in previous.columns)
                    else "TURN_TWO_FAILED"
                    if previous is not None
                    else "TURN_ONE_FAILED"
                ),
            )
            sequence += 1
            yield _completion(request, sequence, "failed", request.version)
            return

        result = outcome_or_error
        self._frame_store.put(result.frame)
        yield _result(request, sequence, result)
        sequence += 1
        yield _completion(request, sequence, "succeeded", result.frame.version)

    async def _route(self, request: AskRequest, sequence: int):
        """Hand a non-published question to the tool router and stream what it did."""
        client = getattr(self._planner, "_mia_client", None)
        run = await asyncio.to_thread(run_tool, request.question, self._catalog, mia_client=client)

        # Only open a stage when a tool was actually selected: a stage that starts and
        # immediately fails reads as a broken product, where a bare refusal reads as an
        # answer.
        if run.choice.tool is not None:
            yield _stage_start(request, sequence, "agentic_analytics")
            sequence += 1
            yield _tool_selected(
                request, sequence, _EVENT_NAME.get(run.choice.tool, run.choice.tool)
            )
            sequence += 1
            yield _stage_end(
                request, sequence, "agentic_analytics", "succeeded" if run.ran else "failed"
            )
            sequence += 1

        # Tool results have no AnalysisFrame yet, and ResultPayload requires one - so the
        # outcome is reported on the error channel rather than faked into a frame.
        yield _error(
            request,
            sequence,
            code="TOOL_RUN_NOT_RENDERABLE" if run.ran else "TOOL_REFUSED",
            message=_tool_summary(run) if run.ran else (run.refusal or "Soru yanıtlanamadı."),
            retryable=False,
        )
        yield _completion(request, sequence + 1, "failed", request.version)


# The contract's tool vocabulary predates the router's; web_url is the same tool.
_EVENT_NAME = {"url_agent": "web_url"}


def _tool_summary(run) -> str:
    """One Turkish line naming the tool, what it found, and on which series."""
    kind = type(run.result).__name__
    if kind == "AnomalyResult":
        body = f"{len(run.result.anomalies)} aykırı gözlem ({run.result.observed_count} gözlemde)"
    elif kind == "ChangeDetectionResult":
        body = f"{len(run.result.breakpoints)} kırılma noktası"
    elif kind == "CausalityResult":
        body = f"nedensellik sonucu: {run.result.status}"
    elif kind == "URLDocument":
        body = f"belge okundu ({run.result.kind})"
    elif kind == "LoadedSeries":
        body = f"seri: {run.result.name}"
    else:
        body = kind
    series = f" [{', '.join(run.series_ids)}]" if run.series_ids else ""
    return (
        f"{run.choice.tool} aracı çalıştı — {body}{series}. "
        "Bu araç sonucu henüz tabloya dönüştürülmüyor."
    )


def answer_text(result: TurnResult | TurnTwoResult | TurnThreeResult, question: str) -> str:
    """The findings as prose, with what had to be disclosed, in Turkish.

    Built from the frame's findings rather than generated, so the answer cannot contain a
    number the analysis did not compute. If the question was not the one turn 1 answers,
    that is said first rather than left for the reader to notice.
    """
    lines: list[str] = []
    if isinstance(result, TurnTwoResult):
        derived = result.frame.columns[-1]
        return f"Turn 2 tamamlandı: {derived.label} kolonu mevcut tabloya eklendi."
    if isinstance(result, TurnThreeResult):
        return result.frame.findings[-1].statement
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


def _result(
    request: AskRequest, sequence: int, result: TurnResult | TurnTwoResult | TurnThreeResult
) -> ResultEvent:
    return ResultEvent(
        **_envelope(request, sequence, result.frame.version),
        type="result",
        payload=ResultPayload(frame=result.frame, answer=answer_text(result, request.question)),
    )


def _error(
    request: AskRequest,
    sequence: int,
    *,
    code: str = "TURN_ONE_FAILED",
    message: str = _FAILED,
    retryable: bool = True,
) -> ErrorEvent:
    return ErrorEvent(
        **_envelope(request, sequence),
        type="error",
        payload=ErrorPayload(code=code, user_message=message, retryable=retryable),
    )


def _completion(request: AskRequest, sequence: int, outcome: str, version: int) -> CompletionEvent:
    return CompletionEvent(
        **_envelope(request, sequence, version),
        type="completion",
        payload=CompletionPayload(outcome=outcome),
    )
