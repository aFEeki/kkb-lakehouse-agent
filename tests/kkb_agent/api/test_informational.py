"""Real runner/provider boundary tests for informational success and state safety."""

import asyncio
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from kkb_agent.agent.analyze import analyze
from kkb_agent.agent.date_window import DateWindowError, parse_window
from kkb_agent.api.contracts import AskRequest, EvidenceItem, ResultPayload
from kkb_agent.api.frame_store import AnalysisFrameStore
from kkb_agent.api.main import create_app
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.config import Settings
from kkb_agent.regression import build_snapshot_database
from kkb_agent.tools.url_safety import UntrustedContent
from kkb_agent.tools.web_search import WebSearchItem, WebSearchResult, WebSearchUnavailableError

SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "fixtures/regression/published_three_turn_snapshot.json"
)


class Search:
    def search(self, question, **kwargs):
        if "kesinti" in question:
            raise WebSearchUnavailableError("secret must not reach browser")
        return WebSearchResult(
            question,
            (WebSearchItem("TCMB duyurusu", "https://example.org/news", "Kaynak özeti"),),
            (),
        )


class Fetcher:
    def fetch(self, url):
        return UntrustedContent(
            url,
            url,
            "text/html",
            b"<html><body><h1>Local document</h1><p>Deterministic evidence.</p></body></html>",
            0,
        )


@pytest.fixture
def catalog(tmp_path):
    db = tmp_path / "fixture.duckdb"
    build_snapshot_database(SNAPSHOT, db)
    return db


@pytest.mark.parametrize(
    "question,tool",
    [
        ("TCMB'nin güncel duyurularını web'de ara.", "web_search"),
        ("https://example.org/report oku", "web_url"),
    ],
)
def test_real_informational_result(catalog, question, tool):
    runner = TurnOneAskRunner(catalog, web_search_tool=Search(), url_fetcher=Fetcher())

    async def collect():
        return [
            e
            async for e in runner.run(AskRequest(analysis_id="test", version=0, question=question))
        ]

    events = asyncio.run(collect())
    assert [e.type for e in events[-2:]] == ["result", "completion"]
    assert events[-1].payload.outcome == "succeeded"
    assert events[-2].payload.frame is None
    assert events[-2].payload.information.tool == tool
    assert events[-2].payload.information.evidence[0].url.startswith("https://example.org/")
    assert [e.payload.stage for e in events if e.type == "stage_start"] == [
        e.payload.stage for e in events if e.type == "stage_end"
    ]


def test_failure_preserves_stored_frame_and_is_sanitized(catalog):
    store = AnalysisFrameStore()
    runner = TurnOneAskRunner(catalog, frame_store=store, web_search_tool=Search())

    async def run(question, version):
        return [
            e
            async for e in runner.run(
                AskRequest(analysis_id="same", version=version, question=question)
            )
        ]

    first = asyncio.run(run("Konut kredisi faizlerini göster", 0))[-2].payload.frame
    failed = asyncio.run(run("kesinti web'de ara", first.version))
    assert failed[-1].payload.outcome == "failed"
    assert "secret" not in "".join(e.model_dump_json() for e in failed)
    assert store.get_current("same", first.version) == first
    assert [e.payload.outcome for e in failed if e.type == "stage_end"] == ["failed"]


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("2021-2025", "2021-01-01", "2025-12-31"),
        ("2021 ile 2025 arasında", "2021-01-01", "2025-12-31"),
        ("2022’den 2024’e", "2022-01-01", "2024-12-31"),
        ("2023", "2023-01-01", "2023-12-31"),
        ("Ocak 2022 - Aralık 2024", "2022-01-01", "2024-12-31"),
    ],
)
def test_date_windows(text, start, end):
    assert parse_window(text, (date(2020, 1, 1), date(2020, 12, 31))) == (
        date.fromisoformat(start),
        date.fromisoformat(end),
    )


def test_invalid_range_and_default():
    default = (date(2020, 1, 1), date(2025, 12, 31))
    assert parse_window("kredileri göster", default) == default
    with pytest.raises(DateWindowError):
        parse_window("2025-2021", default)


def test_generic_window_reaches_real_frame(catalog):
    result = analyze("2022 ile 2024 arasında konut kredilerindeki değişimi göster", catalog)
    assert result.frame.spine.values[0] == date(2022, 1, 1)
    assert result.frame.spine.values[-1] == date(2024, 12, 1)
    assert len(result.frame.spine.values) == 36
    assert "2022-01-01" in result.caveats[0]


def test_bad_evidence_and_empty_result_are_rejected():
    with pytest.raises(ValidationError):
        EvidenceItem(title="unsafe", url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        ResultPayload(answer="no evidence")


def test_transport_closes_stage_on_unexpected_provider_failure(catalog):
    class Broken:
        def search(self, *args, **kwargs):
            raise RuntimeError("secret-provider-detail")

    app = create_app(
        Settings(_env_file=None, duckdb_path=catalog),
        ask_runner=TurnOneAskRunner(catalog, web_search_tool=Broken()),
    )
    with TestClient(app) as client:
        response = client.post(
            "/ask", json={"analysis_id": "a", "version": 0, "question": "web'de ara"}
        )
    assert "secret-provider-detail" not in response.text
    assert '"outcome":"failed"' in response.text
    assert response.text.count("event: stage_start") == response.text.count("event: stage_end") == 1


def test_independent_generic_result_identity_does_not_overwrite_published(catalog):
    store = AnalysisFrameStore()
    runner = TurnOneAskRunner(catalog, frame_store=store)

    async def run(question, version):
        return [
            event
            async for event in runner.run(
                AskRequest(analysis_id="published", version=version, question=question)
            )
        ]

    first = asyncio.run(run("Konut kredisi faizlerini göster", 0))[-2].payload.frame
    events = asyncio.run(run("2022-2024 konut kredilerini göster", first.version))
    result = events[-2]
    assert result.type == "result"
    assert result.analysis_id == result.payload.frame.frame_id == events[-1].analysis_id
    assert result.analysis_id != first.frame_id
    assert store.get_current("published", first.version) == first


def test_empty_catalog_health_is_not_ready(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.get("/health").status_code == 503


def test_generic_model_cannot_silently_rebase_source_values(catalog):
    from datetime import UTC, datetime

    from kkb_agent.frame import AddColumnParameters, IndexColumnParameters, Operation

    class Planner:
        def plan(self, **kwargs):
            key = kwargs["column_keys"][0]
            return (
                Operation(
                    operation_id="add",
                    kind="add_column",
                    timestamp=datetime.now(UTC),
                    source_version=0,
                    resulting_version=1,
                    parameters=AddColumnParameters(
                        column_key=key, series_reference=kwargs["series_references"][0]
                    ),
                ),
                Operation(
                    operation_id="index",
                    kind="index_column",
                    timestamp=datetime.now(UTC),
                    source_version=1,
                    resulting_version=2,
                    parameters=IndexColumnParameters(column_key=key, base_date=date(2022, 1, 1)),
                ),
            )

    result = analyze("2022-2024 konut kredilerini göster", catalog, planner=Planner())
    assert result.planned_by == "script"
    assert all(column.origin == "source" for column in result.frame.columns)
