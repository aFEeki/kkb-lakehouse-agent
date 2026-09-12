import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kkb_agent.agent import (
    ComputedToolResult,
    NarrativeGenerator,
    NarrativeValidationError,
)
from kkb_agent.frame import AnalysisFrame, Column, Finding, Lineage, SourceReference, Spine


def column() -> Column:
    return Column(
        key="loans",
        label="Konut kredileri",
        dtype="number",
        values=(100, 120),
        origin="source",
        lineage=Lineage(
            sources=[SourceReference(source_type="fixture", reference="fixture:loans")]
        ),
    )


def finding(**changes) -> Finding:
    fields = {
        "finding_id": "finding-1",
        "statement": "Reel kredi hacmi incelenen dönemde yatay kaldı.",
        "frame_version": 0,
        "supporting_column_keys": ["loans"],
        "producing_tool": "change_detection",
        "caveats": ["Bu ilişki nedensellik kanıtlamaz."],
    }
    return Finding(**(fields | changes))


def frame(findings=()) -> AnalysisFrame:
    return AnalysisFrame(
        frame_id="frame-narrative",
        spine=Spine(values=[date(2025, 1, 1), date(2025, 2, 1)]),
        columns=[column()],
        findings=findings,
    )


def tool_result(**changes) -> ComputedToolResult:
    fields = {
        "result_id": "result-1",
        "tool_name": "lakehouse",
        "summary": "Konut kredisi hacmi 120 milyon TL olarak hesaplandı.",
        "displayed_values": ["120 milyon TL"],
    }
    return ComputedToolResult(**(fields | changes))


def model_response(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return model_response(next(self.outputs))


class FakeMIAClient:
    chat_model = "test-mia"

    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=self.completions))

    def get_client(self):
        return self.client


def test_computed_value_and_tool_attribution_appear_in_turkish_answer():
    result = tool_result()
    client = FakeMIAClient(
        [
            {
                "claims": [
                    {
                        "text": result.summary,
                        "tool_result_ids": [result.result_id],
                        "finding_ids": [],
                    }
                ],
                "insufficient_evidence": False,
            }
        ]
    )

    narrative = NarrativeGenerator(client).generate(
        user_question="Konut kredisi hacmi nedir?",
        findings=[],
        tool_results=[result],
        frame_context=frame(),
    )

    assert "120 milyon TL" in narrative.answer
    assert "Yanıt:" in narrative.answer
    assert "[Kaynak: lakehouse:result-1]" in narrative.answer
    assert narrative.citations[0].tool_names == ("lakehouse",)
    assert narrative.citations[0].tool_result_ids == ("result-1",)


def test_unsupported_number_and_claim_are_rejected_after_one_retry():
    result = tool_result()
    invalid = {
        "claims": [
            {
                "text": "Konut kredisi hacmi 999 milyon TL olarak hesaplandı.",
                "tool_result_ids": [result.result_id],
                "finding_ids": [],
            }
        ],
        "insufficient_evidence": False,
    }
    client = FakeMIAClient([invalid, invalid])

    with pytest.raises(NarrativeValidationError, match="exact supplied evidence"):
        NarrativeGenerator(client).generate(
            user_question="Konut kredisi hacmi nedir?",
            findings=[],
            tool_results=[result],
            frame_context=frame(),
        )

    assert len(client.completions.calls) == 2


def test_findings_caveats_and_refusal_are_surfaced_with_tool_trace():
    refusal = finding(
        statement="Mevcut kanıtlarla nedensel bir sonuç kurulamaz.",
        producing_tool="causality",
        caveats=["Örneklem nedensellik değerlendirmesi için yetersizdir."],
    )
    context = frame([refusal])
    client = FakeMIAClient(
        [
            {
                "claims": [
                    {
                        "text": refusal.statement,
                        "tool_result_ids": [],
                        "finding_ids": [refusal.finding_id],
                    }
                ],
                "insufficient_evidence": False,
            }
        ]
    )

    narrative = NarrativeGenerator(client).generate(
        user_question="Faiz düşüşü kredi artışına neden oldu mu?",
        findings=context.findings,
        tool_results=[],
        frame_context=context,
    )

    assert refusal.statement in narrative.answer
    assert "Sınırlamalar:" in narrative.answer
    assert refusal.caveats[0] in narrative.answer
    assert narrative.surfaced_caveats == refusal.caveats
    assert narrative.surfaced_finding_ids == (refusal.finding_id,)
    assert narrative.citations[0].tool_names == ("causality",)


def test_superseded_finding_is_not_presented_as_current_claim():
    earlier = finding()
    latest = finding(
        finding_id="finding-2",
        statement="Yeni kanıt önceki değerlendirmeyi desteklemiyor.",
        supersedes="finding-1",
        producing_tool="deflate_column",
        caveats=[],
    )
    context = frame([earlier, latest])
    client = FakeMIAClient(
        [
            {
                "claims": [
                    {
                        "text": latest.statement,
                        "tool_result_ids": [],
                        "finding_ids": [latest.finding_id],
                    }
                ],
                "insufficient_evidence": False,
            }
        ]
    )

    narrative = NarrativeGenerator(client).generate(
        user_question="Son değerlendirme nedir?",
        findings=context.findings,
        tool_results=[],
        frame_context=context,
    )

    assert latest.statement in narrative.answer
    assert earlier.statement not in narrative.answer
    assert narrative.surfaced_finding_ids == (latest.finding_id,)


def test_empty_evidence_returns_honest_answer_without_model_call():
    client = FakeMIAClient([])
    narrative = NarrativeGenerator(client).generate(
        user_question="Ne oldu?",
        findings=[],
        tool_results=[],
        frame_context=frame(),
    )

    assert narrative.insufficient_evidence is True
    assert narrative.claims == ()
    assert narrative.citations == ()
    assert "yeterli hesaplanmış kanıt bulunmuyor" in narrative.answer
    assert client.completions.calls == []


def test_first_invalid_selection_is_corrected_with_one_retry():
    result = tool_result()
    invalid = {
        "claims": [
            {
                "text": result.summary,
                "tool_result_ids": ["unknown-result"],
                "finding_ids": [],
            }
        ],
        "insufficient_evidence": False,
    }
    valid = {
        "claims": [
            {
                "text": result.summary,
                "tool_result_ids": [result.result_id],
                "finding_ids": [],
            }
        ],
        "insufficient_evidence": False,
    }
    client = FakeMIAClient([invalid, valid])
    narrative = NarrativeGenerator(client).generate(
        user_question="Konut kredisi hacmi nedir?",
        findings=[],
        tool_results=[result],
        frame_context=frame(),
    )

    assert narrative.insufficient_evidence is False
    assert len(client.completions.calls) == 2
    assert "Doğrulama hatası" in client.completions.calls[1]["messages"][-1]["content"]


def test_generator_does_not_execute_operations_or_mutate_frame():
    context = frame()
    encoded_before = context.model_dump_json()
    result = tool_result()
    selection = {
        "claims": [
            {
                "text": result.summary,
                "tool_result_ids": [result.result_id],
                "finding_ids": [],
            }
        ],
        "insufficient_evidence": False,
    }
    client = FakeMIAClient([selection])

    with patch("kkb_agent.agent.executor.OperationExecutor.execute") as execute:
        NarrativeGenerator(client).generate(
            user_question="Konut kredisi hacmi nedir?",
            findings=[],
            tool_results=[result],
            frame_context=context,
        )

    execute.assert_not_called()
    assert context.model_dump_json() == encoded_before


def test_strict_json_schema_path_is_used():
    result = tool_result()
    selection = {
        "claims": [
            {
                "text": result.summary,
                "tool_result_ids": [result.result_id],
                "finding_ids": [],
            }
        ],
        "insufficient_evidence": False,
    }
    client = FakeMIAClient([selection])
    NarrativeGenerator(client).generate(
        user_question="Konut kredisi hacmi nedir?",
        findings=[],
        tool_results=[result],
        frame_context=frame(),
    )
    call = client.completions.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
