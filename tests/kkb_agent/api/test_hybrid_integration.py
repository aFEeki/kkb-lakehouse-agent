"""Production injection and published fast path, with an offline real LanceDB index."""

from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from kkb_agent.agent.analyze import analyze
from kkb_agent.agent.turn1 import QUESTION
from kkb_agent.agent.turn2 import QUESTION as QUESTION2
from kkb_agent.agent.turn3 import QUESTION as QUESTION3
from kkb_agent.api.contracts import STREAM_EVENT_ADAPTER
from kkb_agent.api.main import create_app
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.vector_index import SemanticIndex, catalog_rows
from kkb_agent.config import Settings
from kkb_agent.regression import build_snapshot_database

SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "fixtures/regression/published_three_turn_snapshot.json"
)
BALANCE = "bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut"


class Embeddings:
    model_id = "fake"

    def embed_texts(self, texts):
        return [[1, 0] if BALANCE in text else [0, 1] for text in texts]

    def embed_query(self, text):
        return [1, 0]


def test_generic_analyze_injected_index_and_fingerprint(tmp_path):
    database = tmp_path / "catalog.duckdb"
    build_snapshot_database(SNAPSHOT, database)
    index = SemanticIndex(tmp_path / "lance", Embeddings())
    index.build(catalog_rows(database))
    result = analyze(
        "2021-2023 Ev satın almak için kalan borç", database, retrieval=HybridRetrieval(index)
    )
    assert result.retrieval_trace.path == "hybrid"
    assert result.series_ids == (BALANCE,)
    assert len(result.frame.spine.values) == 36
    assert len(result.frame.columns) <= 3


def test_published_three_turns_never_invoke_retrieval(tmp_path):
    database = tmp_path / "catalog.duckdb"
    build_snapshot_database(SNAPSHOT, database)
    retrieval = Mock()
    retrieval.resolve.side_effect = AssertionError("published must not embed")
    runner = TurnOneAskRunner(database, retrieval=retrieval)
    settings = Settings(_env_file=None, data_dir=tmp_path, duckdb_path=database)
    with TestClient(create_app(settings, ask_runner=runner)) as client:
        version = 0
        frames = []
        for question, expected in zip((QUESTION, QUESTION2, QUESTION3), (2, 4, 5), strict=True):
            response = client.post(
                "/ask", json={"analysis_id": "same", "version": version, "question": question}
            )
            events = [
                STREAM_EVENT_ADAPTER.validate_json(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[-1].payload.outcome == "succeeded"
            frame = next(event.payload.frame for event in events if event.type == "result")
            version = frame.version
            assert version == expected
            frames.append(frame)
        assert frames[0].spine == frames[1].spine == frames[2].spine
        assert len(frames[0].spine.values) == 60
    retrieval.resolve.assert_not_called()


def test_enabled_default_runner_wiring_and_index_readiness(tmp_path):
    from kkb_agent.api.main import _default_runner

    database = tmp_path / "catalog.duckdb"
    build_snapshot_database(SNAPSHOT, database)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        duckdb_path=database,
        vector_retrieval_enabled=True,
        mia_api_key="",
        mia_embed_model="fake",
    )
    index = SemanticIndex(settings.lancedb_path, Embeddings())
    index.build(catalog_rows(database))
    runner = _default_runner(settings)
    assert runner._retrieval.index.provider.model_id == "fake"
    with TestClient(create_app(settings, ask_runner=runner)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["semantic_index"] == {"status": "ready", "enabled": True}
    settings.vector_retrieval_enabled = False
    assert _default_runner(settings)._retrieval.index is None


def test_generic_router_forwards_retrieval_only_to_analyze(tmp_path):
    from kkb_agent.agent.router import ToolChoice, run_tool

    database = tmp_path / "catalog.duckdb"
    build_snapshot_database(SNAPSHOT, database)
    index = SemanticIndex(tmp_path / "lance", Embeddings())
    index.build(catalog_rows(database))
    result = run_tool(
        "Ev satın almak için kalan borç",
        database,
        choice=ToolChoice(tool="lakehouse", reason="test", chosen_by="rule"),
        retrieval=HybridRetrieval(index),
    )
    assert result.result.retrieval_trace.path == "hybrid"
