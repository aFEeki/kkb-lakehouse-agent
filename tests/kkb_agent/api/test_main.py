import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kkb_agent.api import AskRequest, CompletionEvent, ErrorEvent, ResultEvent
from kkb_agent.api.contracts import STREAM_EVENT_ADAPTER, StageEndEvent, StageStartEvent

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "api"


class FakeAskRunner:
    def __init__(self, events=(), error=None):
        self.events = tuple(events)
        self.error = error
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


def _fixture_events(name):
    return tuple(
        STREAM_EVENT_ADAPTER.validate_json(line)
        for line in (FIXTURES / name).read_text("utf-8").splitlines()
        if line.strip()
    )


def _parse_sse(response):
    parsed = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        event = STREAM_EVENT_ADAPTER.validate_json(fields["data"])
        assert fields["id"] == event.event_id
        assert fields["event"] == event.type
        parsed.append(event)
    return tuple(parsed)


def _ask_body():
    return json.loads((FIXTURES / "ask-request.json").read_text("utf-8"))


def test_health_without_external_credentials(settings):
    from kkb_agent.api.main import create_app

    app = create_app(settings)
    with (
        patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("No network")),
        patch("kkb_agent.llm.client.OpenAI") as mia,
        TestClient(app) as client,
    ):
        mia.assert_not_called()
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "components": {"application": "ok", "duckdb": "ok", "lancedb": "ok"},
        }
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert (
            client.get("/health", headers={"Origin": "https://example.org"}).headers.get(
                "access-control-allow-origin"
            )
            is None
        )


@pytest.mark.parametrize("component", ["duckdb", "lancedb"])
def test_failure_is_sanitized_and_can_recover(settings, component):
    from kkb_agent.api.main import create_app

    app = create_app(settings)
    with TestClient(app) as client:
        with patch.object(app.state.stores[component], "check", side_effect=RuntimeError("secret")):
            response = client.get("/health")
            assert response.status_code == 503
            assert response.json()["components"][component] == "error"
            assert "secret" not in response.text
        assert client.get("/health").status_code == 200


def test_ask_stream_reuses_contract_and_preserves_event_order(settings):
    from kkb_agent.api.main import create_app

    expected = _fixture_events("successful-stream.jsonl")
    runner = FakeAskRunner(expected)
    app = create_app(settings, ask_runner=runner)

    with TestClient(app) as client:
        response = client.post("/ask", json=_ask_body())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert runner.requests == [AskRequest.model_validate(_ask_body())]

    actual = _parse_sse(response)
    assert actual == expected
    assert any(isinstance(event, StageStartEvent) for event in actual)
    assert any(isinstance(event, StageEndEvent) for event in actual)
    assert {event.payload.stage for event in actual if isinstance(event, StageStartEvent)} == {
        "data_discovery",
        "data_preparation",
        "agentic_analytics",
        "analysis",
        "verification",
    }
    assert any(event.type == "tool_selected" for event in actual)
    assert any(isinstance(event, ResultEvent) for event in actual)
    assert isinstance(actual[-1], CompletionEvent)
    assert actual[-1].payload.outcome == "succeeded"


def test_ask_runner_failure_becomes_safe_failed_stream(settings):
    from kkb_agent.api.main import create_app

    first = _fixture_events("error-stream.jsonl")[0]
    secret = "mia-api-key-must-not-leak"
    runner = FakeAskRunner((first,), RuntimeError(f"Traceback: {secret}"))
    secret_settings = settings.model_copy(update={"mia_api_key": SecretStr(secret)})
    app = create_app(secret_settings, ask_runner=runner)

    with TestClient(app) as client:
        response = client.post("/ask", json=_ask_body())

    events = _parse_sse(response)
    assert [event.type for event in events] == [
        "stage_start",
        "error",
        "stage_end",
        "completion",
    ]
    error = next(event for event in events if isinstance(event, ErrorEvent))
    assert error.payload.code == "ASK_EXECUTION_FAILED"
    assert error.payload.retryable is True
    assert secret not in response.text
    assert "Traceback" not in response.text
    assert events[-1].payload.outcome == "failed"


def test_malformed_ask_uses_fastapi_validation_and_does_not_run(settings):
    from kkb_agent.api.main import create_app

    runner = FakeAskRunner()
    app = create_app(settings, ask_runner=runner)
    malformed = _ask_body() | {"version": "0"}

    with TestClient(app) as client:
        response = client.post("/ask", json=malformed)

    assert response.status_code == 422
    assert runner.requests == []


def test_injected_runner_needs_no_orchestration_mia_network_or_database(settings):
    from kkb_agent.api.main import create_app

    runner = FakeAskRunner(_fixture_events("successful-stream.jsonl"))
    app = create_app(settings, ask_runner=runner)
    forbidden = AssertionError("transport crossed into an implementation dependency")

    with (
        patch("kkb_agent.agent.planner.OperationPlanner.plan", side_effect=forbidden),
        patch("kkb_agent.agent.executor.OperationExecutor.execute", side_effect=forbidden),
        patch("kkb_agent.agent.narrative.NarrativeGenerator.generate", side_effect=forbidden),
        patch("kkb_agent.catalog.series_resolver.resolve_series", side_effect=forbidden),
        patch("kkb_agent.catalog.duckdb_store.DuckDBStore.connect", side_effect=forbidden),
        patch("kkb_agent.llm.client.OpenAI", side_effect=forbidden),
        patch("httpx.HTTPTransport.handle_request", side_effect=forbidden),
        TestClient(app) as client,
    ):
        response = client.post("/ask", json=_ask_body())

    assert response.status_code == 200
    assert _parse_sse(response)[-1].payload.outcome == "succeeded"
