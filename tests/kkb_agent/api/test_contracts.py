import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kkb_agent.api import (
    AskRequest,
    CompletionEvent,
    ErrorEvent,
    ResultEvent,
    StageEndEvent,
    StageStartEvent,
    ToolSelectedEvent,
    ask_request_json_schema,
    stream_event_json_schema,
)
from kkb_agent.api.contracts import STREAM_EVENT_ADAPTER

ROOT = Path(__file__).parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "api"
CONTRACTS = ROOT / "contracts"


def load_ask_request() -> AskRequest:
    return AskRequest.model_validate_json((FIXTURES / "ask-request.json").read_text("utf-8"))


def load_stream(name: str):
    return tuple(
        STREAM_EVENT_ADAPTER.validate_json(line)
        for line in (FIXTURES / name).read_text("utf-8").splitlines()
        if line.strip()
    )


def assert_stream_rules(events, request: AskRequest) -> None:
    assert events
    assert [event.sequence for event in events] == list(range(len(events)))
    assert {event.analysis_id for event in events} == {request.analysis_id}
    assert events[0].frame_version == request.version
    assert [event.frame_version for event in events] == sorted(
        event.frame_version for event in events
    )
    assert [event.occurred_at for event in events] == sorted(event.occurred_at for event in events)
    assert len({event.event_id for event in events}) == len(events)

    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert completions == [events[-1]]
    assert completions[0].frame_version == events[-2].frame_version

    open_stage = None
    for event in events:
        if isinstance(event, StageStartEvent):
            assert open_stage is None
            open_stage = event.payload.stage
        elif isinstance(event, StageEndEvent):
            assert event.payload.stage == open_stage
            open_stage = None
        elif isinstance(event, ToolSelectedEvent):
            assert open_stage is not None
        elif isinstance(event, ResultEvent):
            assert open_stage is None
            assert event.payload.frame.frame_id == event.analysis_id
            assert event.payload.frame.version == event.frame_version
    assert open_stage is None


def test_committed_json_schemas_match_python_contracts():
    assert json.loads((CONTRACTS / "ask-request.schema.json").read_text("utf-8")) == (
        ask_request_json_schema()
    )
    assert json.loads((CONTRACTS / "sse-event.schema.json").read_text("utf-8")) == (
        stream_event_json_schema()
    )


def test_success_fixture_is_valid_and_covers_all_event_types():
    request = load_ask_request()
    events = load_stream("successful-stream.jsonl")

    assert_stream_rules(events, request)
    assert {event.type for event in events} == {
        "stage_start",
        "stage_end",
        "tool_selected",
        "result",
        "completion",
    }
    assert not any(isinstance(event, ErrorEvent) for event in events)
    result = next(event for event in events if isinstance(event, ResultEvent))
    assert [column.key for column in result.payload.frame.columns] == [
        "housing_loans",
        "housing_rate",
        "real_housing_loans",
    ]
    assert result.payload.frame.columns[0].unit.scale == 1_000_000.0
    assert result.payload.frame.columns[2].lineage.parents[0].column_key == "housing_loans"
    assert result.payload.frame.charts[0].column_keys == ("housing_loans", "housing_rate")
    assert events[-1].payload.outcome == "succeeded"
    assert any(isinstance(event, ResultEvent) for event in events[:-1])


def test_error_fixture_is_valid_and_terminates_as_failed():
    request = load_ask_request()
    events = load_stream("error-stream.jsonl")

    assert_stream_rules(events, request)
    assert {event.type for event in events} == {
        "stage_start",
        "stage_end",
        "tool_selected",
        "error",
        "completion",
    }
    assert not any(isinstance(event, ResultEvent) for event in events)
    assert next(event for event in events if isinstance(event, ErrorEvent)).payload.code == (
        "SOURCE_UNAVAILABLE"
    )
    assert events[-1].payload.outcome == "failed"
    assert any(isinstance(event, ErrorEvent) for event in events[:-1])


@pytest.mark.parametrize("field", ["traceback", "stack_trace", "detail"])
def test_error_contract_rejects_internal_exception_fields(field):
    raw = json.loads((FIXTURES / "error-stream.jsonl").read_text("utf-8").splitlines()[2])
    raw["payload"][field] = "secret internal exception"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        STREAM_EVENT_ADAPTER.validate_python(raw)


def test_error_contract_rejects_traceback_content_in_public_message():
    raw = json.loads((FIXTURES / "error-stream.jsonl").read_text("utf-8").splitlines()[2])
    raw["payload"]["user_message"] = "Traceback (most recent call last):\nsecret"

    with pytest.raises(ValidationError, match="single-line public message"):
        STREAM_EVENT_ADAPTER.validate_python(raw)


def test_ask_request_requires_exact_nonnegative_version_and_closed_shape():
    valid = load_ask_request().model_dump()

    with pytest.raises(ValidationError):
        AskRequest.model_validate(valid | {"version": -1})
    with pytest.raises(ValidationError):
        AskRequest.model_validate(valid | {"internal": "not allowed"})
