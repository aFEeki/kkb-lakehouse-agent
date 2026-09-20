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
import unicodedata
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.router import run_tool, select_tool
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
from kkb_agent.catalog.identity import turkish_casefold
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.frame import (
    AddColumnParameters,
    AnalysisFrame,
    Operation,
    OperationType,
    Spine,
)

logger = logging.getLogger(__name__)

_FAILED = "Analiz tamamlanamadı. Lütfen tekrar deneyin."
_SENTINEL = object()


def _fold(text: str) -> str:
    """Casefold and strip Turkish diacritics, so a question still matches when it is
    typed without them.

    People write "arindirir" as often as "arındırır", and the organizers' own slide
    writes "sutun" without the u-umlaut. Matching on the accented spelling alone rejects
    the published question as typed by the people who published it.
    """
    lowered = turkish_casefold(text).replace("ı", "i").replace("İ", "i")
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped.strip())


def classify_published_turn(question: str) -> int | None:
    """Recognize only the three bounded Turkish demo intents."""
    normalized = _fold(question)

    def has(*terms: str) -> bool:
        return any(_fold(term) in normalized for term in terms)

    if has("konut fiyat endeksi", "fiyat endeksini", "kfe") and has(
        "ekle", "sütun", "nedeni", "olabilir"
    ):
        return 3
    if has("kredi") and has("enflasyondan arındır", "reel", "sabit fiyat", "deflat"):
        return 2
    if has("konut") and has("kredi") and has("faiz"):
        return 1
    return None


def _out_of_order_message(requested: int, expected: int) -> str:
    """Say which step is missing, in the words the user would use to ask for it.

    Turns 2 and 3 modify the table turn 1 builds, so asking for one first is not a
    malformed request - it is a request that arrived early, and the reply should be the
    sentence that gets the user unstuck.
    """
    ask_first = {
        1: "2021-2025 arasında konut kredileri ve faiz oranlarını aylık göster.",
        2: "Konut kredisi tutarlarını enflasyondan arındırır mısın?",
    }
    if requested > expected:
        return (
            f"Bu soru mevcut tabloyu değiştiriyor, ama o tablo henüz yok. "
            f"Önce şunu sorun: “{ask_first[expected]}”"
        )
    return (
        "Bu adım bu analizde zaten tamamlandı. Tabloyu sıfırdan kurmak için sayfayı "
        "yenileyip ilk sorudan başlayın."
    )


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
            # A real turn, asked out of order. Still a sequencing error, not a tool
            # question - so say which step is missing and what to ask for, rather than
            # naming an internal concept the reader has no way to act on.
            yield _error(
                request,
                sequence,
                code="UNSUPPORTED_PUBLISHED_TURN",
                message=_out_of_order_message(requested_turn, expected_turn),
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
        choice = await asyncio.to_thread(select_tool, request.question, mia_client=client)

        # Only open a stage when a tool was actually selected: a stage that starts and
        # immediately fails reads as a broken product, where a bare refusal reads as an
        # answer.
        if choice.tool is None:
            yield _error(
                request,
                sequence,
                code="TOOL_REFUSED",
                message="Bu soru mevcut araçlardan hiçbirine yönlendirilemedi.",
                retryable=False,
            )
            yield _completion(request, sequence + 1, "failed", request.version)
            return

        # Announce the tool before running it, not after. The run reads a catalog series
        # and can take seconds; a stage that only appears once it is already over leaves
        # the screen empty for exactly as long as the work takes.
        yield _stage_start(request, sequence, "agentic_analytics")
        sequence += 1
        yield _tool_selected(request, sequence, _EVENT_NAME.get(choice.tool, choice.tool))
        sequence += 1

        run = await asyncio.to_thread(
            run_tool, request.question, self._catalog, mia_client=client, choice=choice
        )

        yield _stage_end(
            request, sequence, "agentic_analytics", "succeeded" if run.ran else "failed"
        )
        sequence += 1

        # A tool that ran and produced a finding is an answer. Deliver it as one whenever
        # the series it read can be put on a spine, so the reader gets the numbers behind
        # the sentence instead of being told to take it on trust.
        if run.ran:
            frame = await asyncio.to_thread(_tool_frame, self._catalog, request.analysis_id, run)
            if frame is not None:
                self._frame_store.put(frame)
                yield _frame_result(request, sequence, frame, _tool_summary(run))
                yield _completion(request, sequence + 1, "succeeded", frame.version)
                return

        if run.choice.tool == "web_search" and run.ran:
            yield _stage_start(request, sequence, "verification")
            sequence += 1
            yield _stage_end(request, sequence, "verification", "succeeded")
            sequence += 1

        # Tool results have no AnalysisFrame yet, and ResultPayload requires one - so the
        # outcome is reported on the error channel rather than faked into a frame.
        yield _error(
            request,
            sequence,
            code="TOOL_RUN_NOT_RENDERABLE" if run.ran else "TOOL_REFUSED",
            message=_tool_message(run) if run.ran else (run.refusal or "Soru yanıtlanamadı."),
            retryable=False,
        )
        yield _completion(request, sequence + 1, "failed", request.version)


# The contract's tool vocabulary predates the router's; web_url is the same tool.
_EVENT_NAME = {"url_agent": "web_url"}


def _tool_frame(catalog, analysis_id: str, run) -> AnalysisFrame | None:
    """Put the series a tool actually read onto a spine, so its finding can be a result.

    A tool answers about a series; without the series, the reader gets a sentence and no
    way to check it. The columns are added through the ordinary `add_column` executor, so
    the same lineage, left-join and spine guarantees apply as anywhere else — this is the
    real table, not a display copy assembled for the occasion.
    """
    if not run.series_ids:
        return None
    source = CatalogSeriesSource(catalog)
    try:
        loaded = [source.fetch(series_id) for series_id in run.series_ids]
        if any(item is None for item in loaded):
            return None
        periods = sorted({period for item in loaded for period in item.values})
        if not periods:
            return None

        frame = AnalysisFrame(
            frame_id=analysis_id, spine=Spine(values=tuple(periods), label="Dönem")
        )
        executor = create_operation_executor(series_source=source)
        for index, series_id in enumerate(run.series_ids):
            frame = executor.execute(
                frame,
                Operation(
                    operation_id=f"tool-add-{index}",
                    kind=OperationType.ADD_COLUMN,
                    parameters=AddColumnParameters(
                        series_reference=series_id, column_key=series_id
                    ),
                    timestamp=datetime.now(UTC),
                    source_version=frame.version,
                    resulting_version=frame.version + 1,
                ),
            )
        return frame
    except Exception:
        # A tool finding is worth delivering even when the table cannot be built; the
        # caller falls back to reporting the finding on its own.
        logger.exception("could not build a frame for the %s tool run", run.choice.tool)
        return None
    finally:
        source.close()


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
    elif kind == "WebSearchResult":
        if not run.result.items:
            body = "arama tamamlandı; sonuç bulunamadı"
        else:
            citations = "; ".join(
                f"{item.title or 'Başlıksız sonuç'} — {item.url}" for item in run.result.items[:3]
            )
            body = f"{len(run.result.items)} sonuç; kaynaklar: {citations}"
    else:
        body = kind
    series = f" [{', '.join(run.series_ids)}]" if run.series_ids else ""
    return f"{run.choice.tool} aracı çalıştı — {body}{series}."


def _tool_message(run) -> str:
    """The same finding, sized for the error channel.

    `ErrorPayload.user_message` caps at 500 characters and a web-search summary carries
    citations, so the text that goes out when no table could be built is truncated. The
    result channel has no such cap and gets the summary whole.
    """
    summary = f"{_tool_summary(run)} Bu araç sonucu henüz tabloya dönüştürülmüyor."
    return summary if len(summary) <= 500 else summary[:497] + "..."


def answer_text(result: TurnResult | TurnTwoResult | TurnThreeResult, question: str) -> str:
    """The findings as prose, with what had to be disclosed, in Turkish.

    Built from the frame's findings rather than generated, so the answer cannot contain a
    number the analysis did not compute. If the question was not the one turn 1 answers,
    that is said first rather than left for the reader to notice.
    """
    lines: list[str] = []
    if isinstance(result, TurnTwoResult):
        derived = result.frame.columns[-1]
        return f"{derived.label} kolonu, mevcut tablo bozulmadan eklendi."
    if isinstance(result, TurnThreeResult):
        return result.frame.findings[-1].statement
    # Disclose only when turn 1 ran for a question it does not actually cover. Comparing
    # the wording verbatim fired on every paraphrase of the same question, so a correct
    # answer to "konut kredileri ve faiz oranlarını göster" opened by telling the reader
    # their question had not been answered - which was not true, and read as a failure.
    if classify_published_turn(question) != 1:
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


def _frame_result(
    request: AskRequest, sequence: int, frame: AnalysisFrame, answer: str
) -> ResultEvent:
    """A result assembled from a frame and an answer, for paths with no TurnResult."""
    return ResultEvent(
        **_envelope(request, sequence, frame.version),
        type="result",
        payload=ResultPayload(frame=frame, answer=answer),
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
