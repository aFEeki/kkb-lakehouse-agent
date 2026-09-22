"""Offline latency invariants: no wall-clock thresholds or external services."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from openai import OpenAI

from kkb_agent.agent.analyze import analyze
from kkb_agent.agent.planner import OperationPlanner, PlannerTimeoutError
from kkb_agent.agent.router import select_tool
from kkb_agent.agent.turn1 import QUESTION
from kkb_agent.agent.turn2 import QUESTION as QUESTION2
from kkb_agent.agent.turn3 import QUESTION as QUESTION3
from kkb_agent.api.contracts import AskRequest
from kkb_agent.api.frame_store import AnalysisFrameStore
from kkb_agent.api.main import _stream_ask
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.config import Settings
from kkb_agent.llm.client import MIAClient
from kkb_agent.regression import build_snapshot_database
from kkb_agent.tools.web_search import WebSearchResult

SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "fixtures/regression/published_three_turn_snapshot.json"
)


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.duckdb"
    build_snapshot_database(SNAPSHOT, path)
    return path


def request(question=QUESTION, version=0):
    return AskRequest(question=question, analysis_id="latency", version=version)


async def collect(runner, req):
    return [event async for event in runner.run(req)]


def balanced(events):
    starts = [e.payload.stage for e in events if e.type == "stage_start"]
    ends = [e.payload.stage for e in events if e.type == "stage_end"]
    assert starts == ends
    assert len(set(e.sequence for e in events)) == len(events)


def test_published_all_three_turns_zero_mia_and_embedding(catalog):
    planner = Mock()
    planner.plan.side_effect = AssertionError("No model planning on published path")
    planner._mia_client.get_client.side_effect = AssertionError("No MIA on published path")
    retrieval = Mock()
    retrieval.resolve.side_effect = AssertionError("No embedding/retrieval on published path")
    runner = TurnOneAskRunner(catalog, planner=planner, retrieval=retrieval)
    frames = []
    version = 0
    for question, expected in zip((QUESTION, QUESTION2, QUESTION3), (2, 4, 5), strict=True):
        events = asyncio.run(collect(runner, request(question, version)))
        balanced(events)
        frame = next(e.payload.frame for e in events if e.type == "result")
        assert frame.version == expected
        frames.append(frame)
        version = frame.version
    assert frames[0].spine == frames[1].spine == frames[2].spine
    assert len(frames[0].spine.values) == 60
    planner.plan.assert_not_called()
    planner._mia_client.get_client.assert_not_called()
    retrieval.resolve.assert_not_called()


@pytest.mark.parametrize(
    "question,tool",
    [
        ("Konut kredisi serisinde anomalileri bul.", "anomaly"),
        ("Konut kredilerinde belirgin kırılma noktaları var mı?", "change_detection"),
        ("TCMB'nin güncel duyurularını web'de ara.", "web_search"),
        ("https://example.org/ oku", "url_agent"),
        ("İki seri arasında nedensellik var mı?", "causality"),
        ("2022 ile 2024 arasında konut kredilerindeki değişimi göster.", "lakehouse"),
        ("Konut kredilerindeki anomalileri göster", "anomaly"),
    ],
)
def test_explicit_intent_has_no_model_wait(question, tool):
    client = Mock()
    client.get_client.side_effect = AssertionError("unnecessary call")
    choice = select_tool(question, mia_client=client)
    assert (choice.tool, choice.chosen_by) == (tool, "rules")
    client.get_client.assert_not_called()


@pytest.mark.parametrize(
    "question",
    ["Konut kredisi bakiyesi nedir?", "Anomali ve kırılma var mı?", "Hava durumunu göster"],
)
def test_ambiguous_model_path_still_available(question):
    client = Mock(chat_model="test")
    client.get_client.return_value.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"tool":"lakehouse","reason":"x"}'))
        ]
    )
    choice = select_tool(question, mia_client=client)
    assert choice.chosen_by == "planner"
    client.get_client.return_value.chat.completions.create.assert_called_once()


@pytest.fixture
def timed_out_client():
    calls = []

    def transport(req):
        calls.append(req)
        assert req.extensions["timeout"] == {
            "connect": 5.0,
            "read": 35.0,
            "write": 10.0,
            "pool": 5.0,
        }
        raise httpx.ReadTimeout("provider-secret", request=req)

    def factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(transport)))

    with patch("kkb_agent.llm.client.OpenAI", side_effect=factory):
        client = MIAClient(Settings(_env_file=None, mia_api_key="offline-test-only"))
        try:
            yield client, calls
        finally:
            client.close()


def test_http_timeout_routes_to_existing_fallback_once(timed_out_client):
    client, calls = timed_out_client
    choice = select_tool("Anomali ve kırılma var mı?", mia_client=client)
    assert choice.tool == "anomaly"
    assert choice.chosen_by == "model_timeout_fallback"
    assert len(calls) == 1  # SDK must not retry the transport failure.
    assert "provider-secret" not in repr(choice)


def test_http_timeout_planning_safe_exception_no_retry(timed_out_client):
    client, calls = timed_out_client
    with pytest.raises(PlannerTimeoutError, match="^MIA operation planning timed out$"):
        OperationPlanner(client).plan("Index", "column=loans", 0)
    assert len(calls) == 1


def test_planner_timeout_keeps_deterministic_fallback(catalog, timed_out_client, caplog):
    client, calls = timed_out_client
    with caplog.at_level("INFO", logger="kkb_agent.agent.analyze"):
        result = analyze(
            "2022-2024 konut kredisi bakiyesini göster", catalog, planner=OperationPlanner(client)
        )
    assert result.planned_by == "script"
    assert len(result.frame.spine.values) == 36
    assert len(calls) == 1
    assert "model_timeout_fallback" in caplog.text
    assert "provider-secret" not in caplog.text


def test_timeout_refusal_does_not_mutate_stored_frame(catalog, timed_out_client):
    client, calls = timed_out_client
    store = AnalysisFrameStore()
    runner = TurnOneAskRunner(catalog, planner=OperationPlanner(client), frame_store=store)
    first = asyncio.run(collect(runner, request()))
    frame = next(e.payload.frame for e in first if e.type == "result")
    snapshot = frame.model_dump_json()
    failed = asyncio.run(collect(runner, request("Bilinmeyen bir soru", frame.version)))
    balanced(failed)
    assert failed[-1].payload.outcome == "failed"
    assert store.get_current("latency", frame.version).model_dump_json() == snapshot
    assert len(calls) == 1
    assert "provider-secret" not in "".join(e.model_dump_json() for e in failed)


@pytest.mark.parametrize("boundary", ["model", "search"])
def test_first_sse_stage_precedes_slow_external_completion(boundary):
    entered, release = threading.Event(), threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(3), "test must release the provider"
        if boundary == "search":
            return WebSearchResult("query", (), ())
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"tool":null,"reason":"no match"}')
                )
            ]
        )

    mia = Mock(chat_model="test")
    mia.get_client.return_value.chat.completions.create.side_effect = slow
    search = Mock()
    search.search.side_effect = slow
    runner = TurnOneAskRunner("unused", planner=OperationPlanner(mia), web_search_tool=search)
    req = request("web'de ara" if boundary == "search" else "Belirsiz soru")

    async def exercise():
        stream = _stream_ask(runner, req)
        first = await anext(stream)
        assert "event: stage_start" in first
        assert not entered.is_set()
        task = asyncio.create_task(_remaining(stream))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            assert not task.done()
        finally:
            release.set()
        rest = await task
        wire = first + "".join(rest)
        events = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
        assert sum(e["type"] == "stage_start" for e in events) == sum(
            e["type"] == "stage_end" for e in events
        )
        assert events[-1]["type"] == "completion"

    asyncio.run(exercise())


async def _remaining(stream):
    return [event async for event in stream]


def test_generic_source_only_skips_model_and_disabled_vectors(catalog):
    planner = Mock()
    planner._mia_client.get_client.side_effect = AssertionError("No MIA")
    planner.plan.side_effect = AssertionError("No planning")
    runner = TurnOneAskRunner(catalog, planner=planner, retrieval=HybridRetrieval())
    with (
        patch("kkb_agent.catalog.vector_index.SemanticIndex.search") as vector,
        patch("kkb_agent.llm.embeddings.MIAEmbeddingProvider.embed_query") as embedding,
    ):
        events = asyncio.run(
            collect(runner, request("2022 ile 2024 arasında konut kredilerindeki değişimi göster."))
        )
    balanced(events)
    frame = next(e.payload.frame for e in events if e.type == "result")
    assert len(frame.spine.values) == 36
    assert len(frame.columns) <= 3
    planner.plan.assert_not_called()
    planner._mia_client.get_client.assert_not_called()
    vector.assert_not_called()
    embedding.assert_not_called()
