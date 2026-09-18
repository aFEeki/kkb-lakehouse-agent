"""Routing a question to one of the brief's tools.

The tools were built, tested and unreachable: five modules with zero call sites outside
their own package. These tests are about the selection being *right*, because the failure
that matters is not an exception - it is a confident answer produced by the wrong tool.
"""

from __future__ import annotations

import pytest

from kkb_agent.agent.router import (
    TOOLS,
    ToolChoice,
    rule_choice,
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

    def test_an_ordinary_data_question_matches_nothing_rather_than_guessing(self):
        """The cue lists are narrow on purpose. A question this router cannot place must
        fall through to the existing honest refusal, not to the nearest tool."""
        assert rule_choice("2021-2025 arası konut kredisi bakiyesini göster").tool is None

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


class TestWhatIsNotBuiltYet:
    def test_web_search_is_selectable_but_not_routable(self):
        """The brief lists six tools and we have five. Selecting it and refusing is honest;
        omitting it would make a web-search question look merely unroutable."""
        assert "web_search" in TOOLS
        assert ToolChoice("web_search", "", "rules").routable is False

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
