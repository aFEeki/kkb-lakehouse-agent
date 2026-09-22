"""MIA-backed emission of validated operations, separate from execution."""

import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime

from openai import APITimeoutError
from pydantic import ValidationError

from kkb_agent.frame import (
    AddColumnParameters,
    DeflateColumnParameters,
    IndexColumnParameters,
    Operation,
    OperationType,
    RevertToParameters,
)
from kkb_agent.llm.client import MIAClient

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
MAX_TOKENS = 1024
MAX_ERROR_CONTEXT_CHARS = 2000


class PlannerError(RuntimeError):
    """Base error for an operation-planning failure."""


class PlannerModelError(PlannerError):
    """The MIA model call failed before a response could be validated."""


class PlannerTimeoutError(PlannerModelError):
    """Actual model I/O timed out; callers may use their existing deterministic fallback."""


class PlannerValidationError(PlannerError):
    """MIA failed to emit a valid operation plan within the retry bound."""


# Which parameter model belongs to which operation kind. Operation validates this pairing
# after the fact; the schema below states it up front, so the model cannot get it wrong.
_PARAMETERS_FOR: dict[OperationType, type] = {
    OperationType.ADD_COLUMN: AddColumnParameters,
    OperationType.DEFLATE_COLUMN: DeflateColumnParameters,
    OperationType.INDEX_COLUMN: IndexColumnParameters,
    OperationType.REVERT_TO: RevertToParameters,
}


def operation_plan_schema(
    series_references: Sequence[str] | None = None,
    column_keys: Sequence[str] | None = None,
    conventions: Sequence[str] | None = None,
    base_dates: Sequence[str] | None = None,
) -> dict:
    """Wrap the frozen Operation JSON Schema for one-or-more operation emission.

    One branch per operation kind, rather than one shape with an untagged `parameters`
    union. The union version is satisfiable by any kind paired with any kind's parameters,
    and the model duly emitted kind "add_column" carrying index_column's parameters -
    valid against the schema, rejected by Operation, two retries, then fallback.

    Pairing them in the schema is what makes `strict: true` do the work: a mismatched plan
    is now unrepresentable rather than merely invalid.

    `series_references` and `column_keys` narrow the free-text identifier fields to the
    values that actually exist. This is not belt-and-braces. Under constrained decoding
    the model satisfies a `"type": "string"` field with the *shortest legal string* - it
    emitted `"series_reference": "b"` however firmly the prompt asked for a verbatim copy,
    and a worked example only made it emit three operations all referring to "b".
    Prompting cannot fix that; removing the choice can.

    For the same reason the model is not asked for operation_id, timestamp or the version
    numbers. Those are bookkeeping, not decisions: it duly emitted operation_id "p" three
    times and the plan was rejected for duplicate ids. The model chooses the sequence of
    (kind, parameters); the planner assigns identity and advances versions contiguously,
    which it can do without error.
    """
    envelope = deepcopy(Operation.model_json_schema())
    definitions = envelope.pop("$defs", {})
    branches = []
    for kind, parameters in _PARAMETERS_FOR.items():
        parameter_schema = deepcopy(parameters.model_json_schema())
        definitions.update(parameter_schema.pop("$defs", {}))
        # Every free-text identifier, not just the obvious two. deflator_column_key and
        # convention_reference minimise exactly the same way: the first plan that got the
        # analysis right still asked to deflate by a column called "t" using a convention
        # called "c".
        for field, allowed in (
            ("series_reference", series_references),
            ("column_key", column_keys),
            ("deflator_column_key", column_keys),
            ("convention_reference", conventions),
            ("base_date", base_dates),
        ):
            if allowed and field in parameter_schema.get("properties", {}):
                parameter_schema["properties"][field] = {"enum": list(allowed)}
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"const": kind.value},
                    "parameters": parameter_schema,
                },
                "required": ["kind", "parameters"],
            }
        )

    return {
        "$defs": definitions,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {"anyOf": branches},
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
        series_references: Sequence[str] | None = None,
        column_keys: Sequence[str] | None = None,
        conventions: Sequence[str] | None = None,
        base_dates: Sequence[str] | None = None,
    ) -> tuple[Operation, ...]:
        """Return a validated, contiguous tuple of frozen Operation objects.

        `series_references` and `column_keys` are the identifiers the caller is willing to
        have referenced. Supplying them makes an invented reference unrepresentable rather
        than merely rejected downstream.
        """
        if not user_intent.strip():
            raise ValueError("user_intent must not be empty")
        if not resolved_context.strip():
            raise ValueError("resolved_context must not be empty")
        if type(current_version) is not int or current_version < 0:
            raise ValueError("current_version must be a non-negative integer")

        messages = self._initial_messages(user_intent, resolved_context, current_version)
        schema = operation_plan_schema(series_references, column_keys, conventions, base_dates)
        failures: list[str] = []
        for attempt in range(2):
            content = self._complete(messages, schema)
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

    def _complete(self, messages: list[dict[str, str]], schema: dict | None = None) -> str:
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
                        "schema": schema or operation_plan_schema(),
                        "strict": True,
                    },
                },
            )
            content = response.choices[0].message.content
        except APITimeoutError:
            raise PlannerTimeoutError("MIA operation planning timed out") from None
        except Exception:
            raise PlannerModelError("MIA operation planning call failed") from None
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
                    "\n\n"
                    # Without these the model emits a single well-formed operation carrying
                    # placeholder identifiers - "series_reference": "b" - which validates
                    # against the schema and refers to nothing.
                    "Identifiers are not free text. Copy every series_reference verbatim "
                    "from the resolved context; never shorten, abbreviate or invent one. "
                    "Use the column_key the context suggests for that series. Only refer to "
                    "a column_key that the context lists as existing or that an earlier "
                    "operation in this same plan creates.\n"
                    "Plan every step the intent needs, not just the first. A column must be "
                    "added before it can be deflated or indexed, and a deflator column must "
                    "be added before it is used as one.\n"
                    "Use the deflation convention string exactly as the context gives it."
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
        """Turn the model's (kind, parameters) choices into frozen Operations.

        Identity and versioning are assigned here rather than parsed. The model decided
        what to do and in what order; numbering it correctly is arithmetic.
        """
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {"operations"}:
            raise ValueError("Planner response must contain only the 'operations' envelope")
        raw_operations = payload["operations"]
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ValueError("Planner response must contain at least one operation")

        stamped = datetime.now(UTC)
        operations: list[Operation] = []
        for offset, item in enumerate(raw_operations):
            if not isinstance(item, dict) or set(item) != {"kind", "parameters"}:
                raise ValueError("Each planned operation must carry only kind and parameters")
            version = current_version + offset
            operations.append(
                Operation.model_validate(
                    {
                        **item,
                        "operation_id": f"plan-{version + 1}-{item['kind']}",
                        "timestamp": stamped,
                        "source_version": version,
                        "resulting_version": version + 1,
                    }
                )
            )
        return tuple(operations)
