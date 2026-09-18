"""Routing a question to one of the brief's tools.

The tools were built, tested and unreachable: five modules with zero call sites outside
their own package. These tests are about the selection being *right*, because the failure
that matters is not an exception - it is a confident answer produced by the wrong tool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kkb_agent.agent.router import (
    TOOLS,
    ToolChoice,
    rule_choice,
    run_tool,
    select_tool,
    tool_choice_schema,
)


class TestTheRules:
    """The deterministic path. It runs whenever MIA is unreachable, so it has to be
    defensible on its own rather than as a token fallback."""

    @pytest.mark.parametrize(
        "question,expected",
        [
            ("Konut kredilerinde aykırı değer var mı?", "anomaly"),
            ("2023'te olağandışı bir hareket oldu mu?", "anomaly"),
            ("Mevduatta yapısal kırılma noktası nerede?", "change_detection"),
            ("Faiz serisinde rejim değişimi var mı?", "change_detection"),
            ("Faiz artışı kredi hacmine neden oldu mu?", "causality"),
            ("Enflasyon ile mevduat arasında nedensellik var mı?", "causality"),
        ],
    )
    def test_each_tool_is_reachable_by_a_plausible_turkish_question(self, question, expected):
        assert rule_choice(question).tool == expected

    def test_a_url_settles_it_before_any_keyword_is_consulted(self):
        """The content has to be read before anything can be said about it - including
        whether it contains an anomaly."""
        choice = rule_choice(
            "https://www.borsaistanbul.com/endeks/xtumy adresinde aykırı değer var mı?"
        )
        assert choice.tool == "url_agent"

    def test_a_question_matching_no_cue_falls_through_rather_than_guessing(self):
        """The cue lists are narrow on purpose. A question this router cannot place must
        reach the existing honest refusal, not the nearest tool. Retrieval will happily
        resolve almost anything to *some* series - "Bugün hava nasıl?" finds hava
        taşımacılığı credits - so routing on resolution alone would answer a weather
        question with loan data."""
        assert rule_choice("Konut kredisi bakiyesi nedir?").tool is None
        assert rule_choice("Bugün hava nasıl?").tool is None

    def test_the_published_turn_one_question_is_not_hijacked_by_causality(self):
        """It contains 'neden', and turn 1 answers it with its own analysis. A cue list
        matching the bare word would have stolen the demo's first question."""
        assert (
            rule_choice("Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?").tool
            is None
        )

    def test_the_reason_names_the_cue_that_fired(self):
        """So a wrong route can be diagnosed from the event rather than by rerunning it."""
        assert "anomali" in rule_choice("Kredilerde anomali var mı?").reason


class TestTheSchema:
    def test_the_tool_name_is_an_enum_over_what_exists(self):
        """An invented tool name should be unrepresentable, not merely rejected later."""
        schema = tool_choice_schema()
        assert set(schema["properties"]["tool"]["enum"]) == {*TOOLS, None}

    def test_null_is_a_permitted_answer(self):
        """Forcing a choice makes 'none of these' the one thing the model cannot say, so
        it picks the closest name instead - the silent mis-route this exists to avoid."""
        assert None in tool_choice_schema()["properties"]["tool"]["enum"]
        assert "null" in tool_choice_schema()["properties"]["tool"]["type"]


class TestSelection:
    def test_without_a_client_it_uses_the_rules_and_says_so(self):
        choice = select_tool("Kredilerde aykırı değer var mı?")
        assert choice.tool == "anomaly"
        assert choice.chosen_by == "rules"

    def test_an_empty_question_is_a_programming_error_not_a_refusal(self):
        with pytest.raises(ValueError):
            select_tool("   ")

    def test_a_model_choice_is_taken_and_attributed_to_the_planner(self):
        choice = select_tool(
            "herhangi bir soru", mia_client=_FakeClient('{"tool":"causality","reason":"iki seri"}')
        )
        assert (choice.tool, choice.chosen_by) == ("causality", "planner")

    def test_a_model_that_fails_falls_back_to_the_rules_rather_than_erroring(self):
        """MIA being down is not a reason to answer nothing, and the weaker path has to be
        visible in `chosen_by`."""
        choice = select_tool("Kredilerde anomali var mı?", mia_client=_FakeClient(boom=True))
        assert (choice.tool, choice.chosen_by) == ("anomaly", "rules")

    def test_a_tool_name_outside_the_vocabulary_is_not_trusted(self):
        choice = select_tool(
            "Kredilerde anomali var mı?",
            mia_client=_FakeClient('{"tool":"sql_injection","reason":"x"}'),
        )
        assert choice.chosen_by == "rules"


class TestRoutability:
    def test_web_search_is_selectable_and_routable(self):
        assert "web_search" in TOOLS
        assert ToolChoice("web_search", "", "rules").routable is True

    def test_a_built_tool_is_routable(self):
        assert ToolChoice("anomaly", "", "rules").routable is True

    def test_no_match_is_not_routable(self):
        assert ToolChoice(None, "", "rules").routable is False


class _FakeClient:
    """Stands in for MIAClient without a network call."""

    chat_model = "test-model"

    def __init__(self, content: str = "{}", *, boom: bool = False):
        self._content, self._boom = content, boom

    def get_client(self):
        return self

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **_kw):
        if self._boom:
            raise RuntimeError("MIA unreachable")
        content = self._content

        class _Msg:
            message = type("M", (), {"content": content})()

        return type("R", (), {"choices": [_Msg()]})()


GOLD = Path(__file__).resolve().parents[3] / "data" / "gold" / "lakehouse.duckdb"
needs_catalog = pytest.mark.skipif(not GOLD.exists(), reason="gold catalog not built")


class TestDispatchWithoutACatalog:
    """The refusals, which are answers rather than errors and must not need data."""

    def test_an_unroutable_question_refuses_in_turkish(self):
        run = run_tool("Bugün hava nasıl?", GOLD)
        assert run.choice.tool is None
        assert run.ran is False
        assert "yönlendirilemedi" in run.refusal

    def test_web_search_runs_through_an_injected_provider_neutral_tool(self):
        from kkb_agent.tools.web_search import WebSearchResult

        class FakeSearchTool:
            def search(self, query, *, language):
                assert query == "İnternette ara"
                assert language == "tr"
                return WebSearchResult(query, (), ())

        run = run_tool(
            "İnternette ara",
            GOLD,
            mia_client=_FakeClient('{"tool":"web_search","reason":"x"}'),
            web_search_tool=FakeSearchTool(),
        )
        assert run.choice.tool == "web_search"
        assert run.ran
        assert run.result.items == ()

    def test_a_url_tool_choice_without_a_url_refuses(self):
        run = run_tool(
            "bir şeyler oku", GOLD, mia_client=_FakeClient('{"tool":"url_agent","reason":"x"}')
        )
        assert "URL bulunamadı" in run.refusal


@needs_catalog
class TestEveryBuiltToolIsReachable:
    """The point of the whole exercise. Before this, all five had zero call sites outside
    their own package: built, tested, and unreachable by asking a question."""

    def test_anomaly(self):
        run = run_tool("Konut kredisi bakiyesinde aykırı değer var mı?", GOLD)
        assert run.choice.tool == "anomaly"
        assert run.ran and run.result.observed_count > 0

    def test_change_detection(self):
        run = run_tool("Konut kredisi bakiyesinde yapısal kırılma nerede?", GOLD)
        assert run.choice.tool == "change_detection"
        assert run.ran and run.result.observation_count > 0

    def test_causality_reaches_the_tool_and_its_refusal_counts_as_running(self):
        """`not_identifiable` is the tool answering, not the router failing. Most Turkish
        macro pairs over 2021-2026 trend together; a causality tool that always returns a
        verdict would be confidently wrong."""
        run = run_tool("Konut kredisi ile konut fiyat endeksi arasında nedensellik var mı?", GOLD)
        assert run.choice.tool == "causality"
        assert run.ran and run.result.status is not None
        assert len(run.series_ids) == 2 and run.series_ids[0] != run.series_ids[1]

    def test_lakehouse_answers_an_explicit_data_request(self):
        run = run_tool("2021-2025 arası konut kredisi bakiyesini göster", GOLD)
        assert run.choice.tool == "lakehouse"
        assert run.ran and run.result.series_id

    def test_a_weather_question_is_refused_rather_than_answered_with_air_transport_loans(self):
        """Retrieval resolves 'hava' to hava taşımacılığı credits. Routing anything that
        resolves to the lakehouse would have answered this with loan data and looked
        computed doing it - which is why the cue lists stay narrow."""
        assert run_tool("Bugün hava nasıl?", GOLD).ran is False
