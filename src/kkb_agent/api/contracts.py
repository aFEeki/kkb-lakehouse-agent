"""Transport-only contracts for ask requests and server-sent event payloads."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, TypeAdapter, field_validator

from kkb_agent.frame import AnalysisFrame
from kkb_agent.frame._base import Identifier, Timestamp, Version

AnalysisId = Identifier
EventId = Identifier
SequenceNumber = Version

StageName = Literal[
    "data_discovery",
    "data_preparation",
    "agentic_analytics",
    "analysis",
    "verification",
]
ToolName = Literal[
    "lakehouse",
    "web_search",
    "web_url",
    "anomaly",
    "causality",
    "change_detection",
]


class APIContract(BaseModel):
    """Immutable, closed wire contract."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class AskRequest(APIContract):
    """Ask against exactly one client-observed analysis version."""

    analysis_id: AnalysisId
    version: Version
    question: Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]


class StageStartPayload(APIContract):
    stage: StageName


class StageEndPayload(APIContract):
    stage: StageName
    outcome: Literal["succeeded", "failed"]


class ToolSelectedPayload(APIContract):
    tool: ToolName


class ResultPayload(APIContract):
    """A self-contained, validated snapshot plus its evidence-bounded answer."""

    frame: AnalysisFrame
    answer: Annotated[str, Field(strict=True, min_length=1)]


class ErrorPayload(APIContract):
    """Safe client-facing failure data; internal exception fields are not accepted."""

    code: Annotated[str, Field(strict=True, min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")]
    user_message: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    retryable: StrictBool

    @field_validator("user_message")
    @classmethod
    def reject_trace_content(cls, value: str) -> str:
        lowered = value.casefold()
        forbidden = ("traceback", "stack trace", "most recent call last")
        if "\n" in value or "\r" in value or any(marker in lowered for marker in forbidden):
            raise ValueError("user_message must be a single-line public message")
        return value


class CompletionPayload(APIContract):
    outcome: Literal["succeeded", "failed"]


class EventEnvelope(APIContract):
    """Fields shared by every event in one ordered analysis stream."""

    event_id: EventId
    analysis_id: AnalysisId
    sequence: SequenceNumber
    frame_version: Version
    occurred_at: Timestamp


class StageStartEvent(EventEnvelope):
    type: Literal["stage_start"]
    payload: StageStartPayload


class StageEndEvent(EventEnvelope):
    type: Literal["stage_end"]
    payload: StageEndPayload


class ToolSelectedEvent(EventEnvelope):
    type: Literal["tool_selected"]
    payload: ToolSelectedPayload


class ResultEvent(EventEnvelope):
    type: Literal["result"]
    payload: ResultPayload


class ErrorEvent(EventEnvelope):
    type: Literal["error"]
    payload: ErrorPayload


class CompletionEvent(EventEnvelope):
    type: Literal["completion"]
    payload: CompletionPayload


StreamEvent = Annotated[
    StageStartEvent
    | StageEndEvent
    | ToolSelectedEvent
    | ResultEvent
    | ErrorEvent
    | CompletionEvent,
    Field(discriminator="type"),
]

STREAM_EVENT_ADAPTER = TypeAdapter(StreamEvent)


def ask_request_json_schema() -> dict:
    """Return the language-neutral ask request contract."""

    return AskRequest.model_json_schema()


def stream_event_json_schema() -> dict:
    """Return the language-neutral discriminated event contract."""

    return STREAM_EVENT_ADAPTER.json_schema()
