"""Public API transport contracts; runtime routes live in :mod:`kkb_agent.api.main`."""

from kkb_agent.api.contracts import (
    AskRequest,
    CompletionEvent,
    ErrorEvent,
    ResultEvent,
    StageEndEvent,
    StageStartEvent,
    StreamEvent,
    ToolSelectedEvent,
    ask_request_json_schema,
    stream_event_json_schema,
)

__all__ = [
    "AskRequest",
    "CompletionEvent",
    "ErrorEvent",
    "ResultEvent",
    "StageEndEvent",
    "StageStartEvent",
    "StreamEvent",
    "ToolSelectedEvent",
    "ask_request_json_schema",
    "stream_event_json_schema",
]
