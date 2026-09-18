"""SCRUM-69/44 - turn 1 streamed over the ask contract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest
from fastapi.testclient import TestClient

from kkb_agent.api.contracts import STREAM_EVENT_ADAPTER, AskRequest
from kkb_agent.api.frame_store import AnalysisFrameStore
from kkb_agent.api.main import _turn1_catalog_ready, create_app
from kkb_agent.api.turn1_runner import TurnOneAskRunner
from kkb_agent.catalog.schema import CATALOG_DDL, OBSERVATIONS_DDL
from kkb_agent.frame import AnalysisFrame, Spine
from kkb_agent.regression import build_snapshot_database

GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "lakehouse.duckdb"
needs_catalog = pytest.mark.skipif(
    not _turn1_catalog_ready(GOLD), reason="populated gold catalog not built"
)

QUESTION = "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"

RATE_SERIES_ID = "evds.TP.KTF12"


def _derived_catalog(tmp_path, *, drop_column=None, drop_rate=False):
    """A copy of the gold catalog with one thing turn 1 needs taken away.

    Built from the real catalog rather than hand-rolled so the fixture cannot drift from
    the schema: every column except the dropped one is whatever gold actually has.
    """
    database = tmp_path / f"derived-{drop_column or 'rate'}.duckdb"
    source = duckdb.connect(str(GOLD), read_only=True)
    columns = [
        row[0]
        for row in source.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'series_catalog' ORDER BY ordinal_position"
        ).fetchall()
        if row[0] != drop_column
    ]
    source.close()

    connection = duckdb.connect(str(database))
    connection.execute(f"ATTACH '{GOLD}' AS gold (READ_ONLY)")
    connection.execute(
        f"CREATE TABLE series_catalog AS SELECT {', '.join(columns)} FROM gold.series_catalog"
    )
    predicate = "WHERE series_id <> ?" if drop_rate else ""
    connection.execute(
        f"CREATE TABLE series_observations AS SELECT * FROM gold.series_observations {predicate}",
        [RATE_SERIES_ID] if drop_rate else [],
    )
    connection.close()
    return database


def _parse_sse(response):
    return tuple(
        STREAM_EVENT_ADAPTER.validate_json(
            next(line[6:] for line in block.splitlines() if line.startswith("data: "))
        )
        for block in response.text.strip().split("\n\n")
    )


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
def test_an_unrelated_initial_question_is_refused():
    import asyncio

    events = asyncio.run(collect(TurnOneAskRunner(GOLD), question="Mevduat faizleri nedir?"))
    assert [event.type for event in events] == ["error", "completion"]
    # The refusal now comes from the router, which considered the question and found no
    # tool for it, rather than from "this is not a published turn".
    assert events[0].payload.code == "TOOL_REFUSED"


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

    def test_an_empty_duckdb_does_not_count_as_ready(self, tmp_path):
        from kkb_agent.api.main import _turn1_catalog_ready

        database = tmp_path / "empty.duckdb"
        duckdb.connect(str(database)).close()
        assert _turn1_catalog_ready(database) is False

    def test_empty_required_tables_do_not_count_as_ready(self, tmp_path):
        from kkb_agent.api.main import _turn1_catalog_ready

        database = tmp_path / "unpopulated.duckdb"
        connection = duckdb.connect(str(database))
        connection.execute(CATALOG_DDL)
        connection.execute(OBSERVATIONS_DDL)
        connection.close()
        assert _turn1_catalog_ready(database) is False

    @needs_catalog
    @pytest.mark.parametrize(
        "drop_column,drop_rate,why",
        [
            ("nonzero_observations", False, "turn 1 retrieval reads it"),
            ("raw_label", False, "turn 1 retrieval reads it"),
            ("source_hash", False, "CatalogSeriesSource reads it for lineage"),
            (None, True, "turn 1 requires this exact rate series"),
        ],
    )
    def test_a_catalog_short_of_what_turn_one_needs_is_not_ready(
        self, tmp_path, drop_column, drop_rate, why
    ):
        """The published data snapshot was built one commit before `nonzero_observations`
        existed. The old check - tables present, at least one row - passed it, so the API
        declared itself ready and every request opened a stage and then died inside it.

        Each case here is a catalog that the old check accepted and turn 1 cannot use. The
        two column lists overlap without either containing the other, so a catalog can
        satisfy one stage's query and fail the next one's.
        """
        database = _derived_catalog(tmp_path, drop_column=drop_column, drop_rate=drop_rate)
        assert _turn1_catalog_ready(database) is False, why

    @needs_catalog
    def test_an_unusable_catalog_never_opens_a_stage_it_cannot_finish(self, tmp_path):
        """Refusing up front and failing mid-stream are both "no answer", but only one of
        them looks like a broken product. Nothing may start that cannot finish."""
        from kkb_agent.config import Settings

        database = _derived_catalog(tmp_path, drop_column="nonzero_observations")
        client = TestClient(create_app(Settings(duckdb_path=database)))
        response = client.post(
            "/ask", json={"analysis_id": "a", "version": 0, "question": QUESTION}
        )
        types = [
            line.split(": ", 1)[1]
            for line in response.text.splitlines()
            if line.startswith("event:")
        ]
        assert types == ["error", "completion"]

    @needs_catalog
    def test_the_real_catalog_is_ready(self):
        """The guard against writing a check so strict it rejects working data."""
        assert _turn1_catalog_ready(GOLD) is True

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


class TestThePlannerIsActuallyWiredIn:
    """The planner existed, was tested, and nothing ever constructed one - so every
    request served through the API ran the scripted fallback and the system demonstrated
    a script rather than an agent. These assert the wiring, because a planner nobody
    builds fails silently: the answers stay correct and only `planned_by` gives it away.
    """

    @staticmethod
    def _settings(key: str, **kw):
        from kkb_agent.config import Settings

        return Settings(_env_file=None, mia_api_key=key, **kw)

    def test_an_unconfigured_environment_plans_with_the_script(self):
        """Absent credentials are a deployment fact, not an error - and this is what keeps
        CI offline."""
        from kkb_agent.api.main import _planner_for

        assert _planner_for(self._settings("")) is None

    def test_the_example_placeholder_key_does_not_count_as_configured(self):
        """Left as shipped, it would cost three failed model attempts per request before
        the fallback ran."""
        from kkb_agent.api.main import _planner_for

        assert _planner_for(self._settings("API_KEYINIZ")) is None

    def test_a_configured_environment_builds_a_model_planner(self):
        from kkb_agent.agent.planner import OperationPlanner
        from kkb_agent.api.main import _planner_for

        assert isinstance(_planner_for(self._settings("sk-not-a-real-key")), OperationPlanner)

    @needs_catalog
    def test_the_runner_the_app_serves_carries_that_planner(self):
        """The gap was between building a planner and handing it to the runner, so assert
        the far end rather than the near one."""
        from kkb_agent.agent.planner import OperationPlanner
        from kkb_agent.api.main import _default_runner

        runner = _default_runner(self._settings("sk-not-a-real-key", duckdb_path=GOLD))
        assert isinstance(runner._planner, OperationPlanner)

        offline = _default_runner(self._settings("", duckdb_path=GOLD))
        assert offline._planner is None


def test_runner_passes_request_identity_to_the_frame():
    import asyncio

    frame = AnalysisFrame(frame_id="t", spine=Spine(values=[]))
    fake_result = type(
        "Result",
        (),
        {"frame": frame, "caveats": (), "planned_by": "script", "plan": ()},
    )()
    store = AnalysisFrameStore()
    with patch("kkb_agent.api.turn1_runner.build_turn1", return_value=fake_result) as build:
        events = asyncio.run(
            collect(TurnOneAskRunner("unused.duckdb", frame_store=store), question=QUESTION)
        )

    assert build.call_args.kwargs["frame_id"] == "t"
    result = next(event for event in events if event.type == "result")
    assert result.analysis_id == result.payload.frame.frame_id == "t"
    assert store.get("t", result.frame_version) == result.payload.frame


def test_runner_rejects_unknown_continuation_version_without_starting_work():
    import asyncio

    with patch("kkb_agent.api.turn1_runner.build_turn1") as build:
        events = asyncio.run(collect(TurnOneAskRunner("unused.duckdb"), version=1))

    build.assert_not_called()
    assert [event.type for event in events] == ["error", "completion"]
    assert events[0].payload.code == "ANALYSIS_VERSION_NOT_FOUND"
    assert events[0].payload.retryable is False
    assert {event.frame_version for event in events} == {1}


def test_a_stage_started_before_failure_is_closed_and_error_is_sanitized(tmp_path):
    import asyncio

    database = tmp_path / "broken.duckdb"
    duckdb.connect(str(database)).close()
    events = asyncio.run(collect(TurnOneAskRunner(database)))

    assert [event.type for event in events] == [
        "stage_start",
        "tool_selected",
        "stage_end",
        "error",
        "completion",
    ]
    assert events[0].payload.stage == events[2].payload.stage == "data_discovery"
    assert events[2].payload.outcome == "failed"
    assert events[3].payload.code == "TURN_ONE_FAILED"
    assert "series_catalog" not in events[3].payload.user_message
    assert events[-1].payload.outcome == "failed"


def test_real_three_request_api_flow_uses_one_store(tmp_path):
    from kkb_agent.agent.turn1 import BALANCE_KEY, RATE_KEY
    from kkb_agent.agent.turn2 import CPI_KEY
    from kkb_agent.agent.turn2 import QUESTION as TURN_TWO_QUESTION
    from kkb_agent.agent.turn3 import HPI_KEY
    from kkb_agent.agent.turn3 import QUESTION as TURN_THREE_QUESTION
    from kkb_agent.config import Settings

    database = tmp_path / "three-turn.duckdb"
    snapshot = (
        Path(__file__).resolve().parents[2]
        / "fixtures/regression/published_three_turn_snapshot.json"
    )
    build_snapshot_database(snapshot, database)
    app = create_app(Settings(duckdb_path=database))
    analysis_id = "three-turn-fixture"
    with TestClient(app) as client:
        first = _parse_sse(
            client.post(
                "/ask", json={"analysis_id": analysis_id, "version": 0, "question": QUESTION}
            )
        )
        frame1 = next(event.payload.frame for event in first if event.type == "result")
        unrelated = _parse_sse(
            client.post(
                "/ask",
                json={
                    "analysis_id": analysis_id,
                    "version": frame1.version,
                    "question": "Mevduat faizleri hakkında ne düşünüyorsun?",
                },
            )
        )
        assert [event.type for event in unrelated] == ["error", "completion"]
        assert unrelated[0].payload.code == "TOOL_REFUSED"
        assert unrelated[-1].frame_version == frame1.version
        second = _parse_sse(
            client.post(
                "/ask",
                json={
                    "analysis_id": analysis_id,
                    "version": frame1.version,
                    "question": TURN_TWO_QUESTION,
                },
            )
        )
        frame2 = next(event.payload.frame for event in second if event.type == "result")
        third = _parse_sse(
            client.post(
                "/ask",
                json={
                    "analysis_id": analysis_id,
                    "version": frame2.version,
                    "question": TURN_THREE_QUESTION,
                },
            )
        )
        frame3 = next(event.payload.frame for event in third if event.type == "result")

    assert [column.key for column in frame1.columns] == [BALANCE_KEY, RATE_KEY]
    assert (frame1.version, frame2.version, frame3.version) == (2, 4, 5)
    assert frame2.columns[:2] == frame1.columns and frame2.columns[2].key == CPI_KEY
    assert frame3.columns[:-1] == frame2.columns and frame3.columns[-1].key == HPI_KEY
    assert frame3.spine == frame2.spine == frame1.spine
    assert frame3.findings[-1].supersedes == "f-decline"
    assert frame3.findings[-1].spine_range is not None
    assert [event.type for event in third[-2:]] == ["result", "completion"]


@needs_catalog
class TestNonPublishedQuestionsReachTheRouter:
    """Before this, anything outside the three demo intents was refused outright."""

    @staticmethod
    def _events(question: str):
        import asyncio

        return asyncio.run(collect(TurnOneAskRunner(GOLD), question=question))

    def test_a_tool_question_selects_a_tool_instead_of_being_refused(self):
        events = self._events("Konut kredisi bakiyesinde aykırı değer var mı?")
        assert [e.payload.tool for e in events if e.type == "tool_selected"] == ["anomaly"]

    def test_a_url_question_reports_the_contract_name_for_the_url_agent(self):
        events = self._events("https://example.org/x.pdf oku")
        assert [e.payload.tool for e in events if e.type == "tool_selected"] == ["web_url"]

    def test_an_unroutable_question_still_refuses(self):
        events = self._events("Bugün hava nasıl?")
        assert [e.type for e in events] == ["error", "completion"]
        assert events[0].payload.code == "TOOL_REFUSED"

    def test_the_published_turn_is_untouched(self):
        events = self._events(QUESTION)
        assert events[-2].type == "result"

    def test_web_search_evidence_is_cited_after_a_verification_stage(self):
        from kkb_agent.agent.router import ToolChoice, ToolRun
        from kkb_agent.tools.web_search import WebSearchEvidence, WebSearchItem, WebSearchResult

        result = WebSearchResult(
            query="güncel haber",
            items=(WebSearchItem("TCMB duyurusu", "https://www.tcmb.gov.tr/duyuru", "özet"),),
            evidence=(WebSearchEvidence("TCMB duyurusu", "https://www.tcmb.gov.tr/duyuru"),),
        )
        run = ToolRun(ToolChoice("web_search", "test", "rules"), result=result)
        with patch("kkb_agent.api.turn1_runner.run_tool", return_value=run):
            events = self._events("Güncel haberi internette ara")

        started = [event.payload.stage for event in events if event.type == "stage_start"]
        assert started == ["agentic_analytics", "verification"]
        assert events[-2].payload.code == "TOOL_RUN_NOT_RENDERABLE"
        assert "TCMB duyurusu" in events[-2].payload.user_message
        assert "https://www.tcmb.gov.tr/duyuru" in events[-2].payload.user_message
        assert events[-1].type == "completion"

    def test_web_search_failure_is_a_safe_refusal(self):
        from kkb_agent.agent.router import ToolChoice, ToolRun

        run = ToolRun(
            ToolChoice("web_search", "test", "rules"),
            refusal="Web araması şu anda kullanılamıyor.",
        )
        with patch("kkb_agent.api.turn1_runner.run_tool", return_value=run):
            events = self._events("Güncel haberi internette ara")

        assert events[-2].payload.code == "TOOL_REFUSED"
        assert events[-2].payload.user_message == "Web araması şu anda kullanılamıyor."
        assert "Traceback" not in events[-2].payload.user_message
        assert events[-1].payload.outcome == "failed"
