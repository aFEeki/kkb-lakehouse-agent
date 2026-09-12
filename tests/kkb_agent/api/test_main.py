from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


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
