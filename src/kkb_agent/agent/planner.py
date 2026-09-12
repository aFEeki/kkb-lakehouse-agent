"""MIA-backed emission of validated operations, separate from execution."""

import json
from copy import deepcopy

from pydantic import ValidationError

from kkb_agent.frame import Operation
from kkb_agent.llm.client import MIAClient

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
MAX_TOKENS = 1024
MAX_ERROR_CONTEXT_CHARS = 2000


class PlannerError(RuntimeError):
    """Base error for an operation-planning failure."""


class PlannerModelError(PlannerError):
    """The MIA model call failed before a response could be validated."""


class PlannerValidationError(PlannerError):
    """MIA failed to emit a valid operation plan within the retry bound."""


def operation_plan_schema() -> dict:
    """Wrap the frozen Operation JSON Schema for one-or-more operation emission."""
    operation_schema = deepcopy(Operation.model_json_schema())
    definitions = operation_schema.pop("$defs", {})
    return {
        "$defs": definitions,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": operation_schema,
            }
        },
        "required": ["operations"],
    }


class OperationPlanner:
    """Ask MIA for operations and validate them without executing anything."""

    def __init__(self, mia_client: MIAClient):
        self._mia_client = mia_client

    def plan(
        self,
        user_intent: str,
        resolved_context: str,
        current_version: int,
    ) -> tuple[Operation, ...]:
        """Return a validated, contiguous tuple of frozen Operation objects."""
        if not user_intent.strip():
            raise ValueError("user_intent must not be empty")
        if not resolved_context.strip():
            raise ValueError("resolved_context must not be empty")
        if type(current_version) is not int or current_version < 0:
            raise ValueError("current_version must be a non-negative integer")

        messages = self._initial_messages(user_intent, resolved_context, current_version)
        failures: list[str] = []
        for attempt in range(2):
            content = self._complete(messages)
            try:
                return self._validate_content(content, current_version)
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
                context = str(exc)[:MAX_ERROR_CONTEXT_CHARS]
                failures.append(context)
                if attempt == 0:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content or ""},
                        {
                            "role": "user",
                            "content": (
                                "The previous response was invalid. Emit only a corrected JSON "
                                "object matching the supplied schema. Validation error:\n" + context
                            ),
                        },
                    ]

        raise PlannerValidationError(
            "MIA emitted an invalid operation plan twice: " + " | ".join(failures)
        )

    def _complete(self, messages: list[dict[str, str]]) -> str:
        try:
            response = self._mia_client.get_client().chat.completions.create(
                model=self._mia_client.chat_model,
                messages=messages,
                max_tokens=MAX_TOKENS,
                extra_body=NO_THINK,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "operation_plan",
                        "schema": operation_plan_schema(),
                        "strict": True,
                    },
                },
            )
            content = response.choices[0].message.content
        except Exception as exc:
            raise PlannerModelError(f"MIA operation planning call failed: {exc}") from exc
        return content if isinstance(content, str) else ""

    @staticmethod
    def _initial_messages(
        user_intent: str, resolved_context: str, current_version: int
    ) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Emit operations only. Never calculate values, return analysis results, "
                    "mutate a frame, or invent an operation. Use only add_column, "
                    "deflate_column, index_column, and revert_to. Every item must satisfy the "
                    "provided Operation JSON Schema. For multiple operations, start at current "
                    "version and advance source_version/resulting_version contiguously by one."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Current frame version: {current_version}\n"
                    f"Resolved context:\n{resolved_context}\n"
                    f"User intent:\n{user_intent}"
                ),
            },
        ]

    @staticmethod
    def _validate_content(content: str, current_version: int) -> tuple[Operation, ...]:
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {"operations"}:
            raise ValueError("Planner response must contain only the 'operations' envelope")
        raw_operations = payload["operations"]
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ValueError("Planner response must contain at least one operation")

        operations = tuple(Operation.model_validate(item) for item in raw_operations)
        expected_source = current_version
        operation_ids: set[str] = set()
        for operation in operations:
            if operation.source_version != expected_source:
                raise ValueError(
                    f"Operation {operation.operation_id!r} targets source version "
                    f"{operation.source_version}; expected {expected_source}"
                )
            if operation.operation_id in operation_ids:
                raise ValueError(f"Duplicate planned operation ID {operation.operation_id!r}")
            operation_ids.add(operation.operation_id)
            expected_source = operation.resulting_version
        return operations
