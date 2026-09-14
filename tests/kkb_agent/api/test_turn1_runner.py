"""SCRUM-69/44 - turn 1 streamed over the ask contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kkb_agent.api.contracts import AskRequest
from kkb_agent.api.main import create_app
from kkb_agent.api.turn1_runner import TurnOneAskRunner

GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "lakehouse.duckdb"
needs_catalog = pytest.mark.skipif(not GOLD.exists(), reason="gold catalog not built")

QUESTION = "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"


async def collect(runner, question: str = QUESTION, version: int = 0):
    request = AskRequest(analysis_id="t", version=version, question=question)
    return [event async for event in runner.run(request)]


@pytest.fixture(scope="module")
def events():
    """Streamed once: it opens the catalog and runs the whole turn."""
    import asyncio

    return asyncio.run(collect(TurnOneAskRunner(GOLD)))


@needs_catalog
class TestTheStream:
    def test_it_reports_all_five_stages_of_the_brief(self, events):
        started = [e.payload.stage for e in events if e.type == "stage_start"]
        assert started == [
            "data_discovery",
            "data_preparation",
            "agentic_analytics",
            "analysis",
            "verification",
        ]

    def test_every_stage_that_started_also_ended(self, events):
        started = [e.payload.stage for e in events if e.type == "stage_start"]
        ended = [e.payload.stage for e in events if e.type == "stage_end"]
        assert started == ended
        assert all(e.payload.outcome == "succeeded" for e in events if e.type == "stage_end")

    def test_sequence_numbers_are_contiguous_from_zero(self, events):
        """A client reassembles the stream by sequence; a gap is indistinguishable from a
        dropped event."""
        assert [e.sequence for e in events] == list(range(len(events)))

    def test_the_catalog_query_is_attributed_to_the_lakehouse_tool(self, events):
        tools = [e.payload.tool for e in events if e.type == "tool_selected"]
        assert tools == ["lakehouse"]

    def test_it_ends_with_a_result_then_a_completion(self, events):
        assert [e.type for e in events[-2:]] == ["result", "completion"]
        assert events[-1].payload.outcome == "succeeded"

    def test_the_result_carries_the_frame_the_answer_was_computed_from(self, events):
        result = next(e for e in events if e.type == "result")
        assert len(result.payload.frame.spine.values) == 60
        assert result.payload.frame.findings
        assert result.payload.answer.strip()

    def test_the_frame_version_advances_only_at_the_result(self, events):
        """Stage events describe work on the version the client asked about; the new
        version exists only once there is a frame to show for it."""
        assert {e.frame_version for e in events if e.type.startswith("stage")} == {0}
        assert next(e for e in events if e.type == "result").frame_version > 0

    def test_the_answer_contains_only_computed_statements(self, events):
        """Built from findings rather than generated, so it cannot carry a number the
        analysis did not produce."""
        result = next(e for e in events if e.type == "result")
        for finding in result.payload.frame.findings:
            assert finding.statement in result.payload.answer


@needs_catalog
def test_a_different_question_is_answered_but_the_substitution_is_disclosed():
    """Turn 1 answers one published question. Returning its findings for a question about
    something else without saying so would be the worst of both options."""
    import asyncio

    events = asyncio.run(collect(TurnOneAskRunner(GOLD), question="Mevduat faizleri nedir?"))
    answer = next(e for e in events if e.type == "result").payload.answer
    assert "yalnızca şu soruyu yanıtlıyor" in answer


class TestAppWiring:
    def test_a_missing_catalog_degrades_cleanly_rather_than_raising(self, tmp_path):
        """A missing catalog is a deployment fact, not a bug. It must produce an error
        event and a failed completion, not a stack trace per request."""
        from kkb_agent.config import Settings

        settings = Settings(duckdb_path=tmp_path / "absent.duckdb")
        client = TestClient(create_app(settings))
        response = client.post(
            "/ask", json={"analysis_id": "a", "version": 0, "question": QUESTION}
        )
        assert response.status_code == 200
        assert "event: error" in response.text
        assert "event: completion" in response.text

    @needs_catalog
    def test_ask_streams_server_sent_events(self):
        # The catalog path is passed explicitly: conftest chdirs every test into a tmp
        # directory so nothing reads the developer's .env or real storage by accident, so
        # a relative default would resolve to an empty directory here.
        from kkb_agent.config import Settings

        client = TestClient(create_app(Settings(duckdb_path=GOLD)))
        response = client.post(
            "/ask", json={"analysis_id": "a", "version": 0, "question": QUESTION}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        types = [
            line.split(": ", 1)[1]
            for line in response.text.splitlines()
            if line.startswith("event:")
        ]
        assert types[0] == "stage_start"
        assert types[-2:] == ["result", "completion"]
