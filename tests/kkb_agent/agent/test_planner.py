import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kkb_agent.agent import (
    OperationPlanner,
    PlannerModelError,
    PlannerValidationError,
    operation_plan_schema,
)
from kkb_agent.frame import Operation, OperationType


def operation_payload(kind="index_column", source_version=0, **changes):
    """One planned operation as the model now emits it: kind and parameters only.

    operation_id, timestamp and the version numbers used to be the model's to emit. They
    are bookkeeping, and under constrained decoding the model minimised them - three
    operations all called "p" - so the planner assigns them instead.
    """
    default_parameters = {
        "index_column": {"column_key": "loans", "base_date": "2025-01-01"},
        "deflate_column": {
            "column_key": "loans",
            "deflator_column_key": "cpi",
            "base_date": "2025-01-01",
            "convention_reference": "cpi_base_period_constant_prices_v1",
        },
        "revert_to": {"target_version": 0},
        "add_column": {"series_reference": "catalog:cpi", "column_key": "cpi"},
    }
    parameters = changes.pop("parameters", default_parameters.get(kind, {}))
    changes.pop("operation_id", None)
    changes.pop("source_version", None)
    return {"kind": kind, "parameters": parameters} | changes


def response(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = next(self.outputs)
        if isinstance(output, Exception):
            raise output
        return response(output)


class FakeMIAClient:
    chat_model = "test-mia-model"

    def __init__(self, outputs):
        self.completions = FakeCompletions(outputs)
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=self.completions))

    def get_client(self):
        return self.client


def plan(outputs, current_version=0):
    client = FakeMIAClient(outputs)
    result = OperationPlanner(client).plan(
        "Apply the requested table operation.",
        "Resolved column keys and dates.",
        current_version,
    )
    return result, client


@pytest.mark.parametrize(
    "kind,source_version",
    [
        ("add_column", 0),
        ("index_column", 0),
        ("deflate_column", 0),
        ("revert_to", 1),
    ],
)
def test_valid_frozen_operation_emission(kind, source_version):
    operations, client = plan(
        [{"operations": [operation_payload(kind, source_version)]}],
        current_version=source_version,
    )

    assert len(operations) == 1
    assert isinstance(operations[0], Operation)
    assert operations[0].kind is OperationType(kind)
    assert len(client.completions.calls) == 1


def test_multiple_operations_are_validated_in_contiguous_execution_order():
    payload = {
        "operations": [
            operation_payload("index_column", 2),
            operation_payload(
                "deflate_column",
                3,
                operation_id="op-3",
            ),
        ]
    }
    operations, _ = plan([payload], current_version=2)
    assert [operation.source_version for operation in operations] == [2, 3]
    assert [operation.resulting_version for operation in operations] == [3, 4]


@pytest.mark.parametrize(
    "invalid",
    [
        {"operations": [operation_payload(parameters={"column_key": "loans"})]},
        {
            "operations": [
                operation_payload(
                    kind="arbitrary_python",
                    parameters={"code": "frame['x'] = frame['x'] * 2"},
                )
            ]
        },
    ],
)
def test_invalid_schema_or_unknown_operation_fails_after_one_retry(invalid):
    client = FakeMIAClient([invalid, invalid])
    planner = OperationPlanner(client)

    with pytest.raises(PlannerValidationError, match="invalid operation plan twice"):
        planner.plan("Do something", "Resolved context", 0)

    assert len(client.completions.calls) == 2


def test_first_invalid_response_then_successful_correction():
    invalid = {"operations": [operation_payload(parameters={"column_key": "loans"})]}
    valid = {"operations": [operation_payload("index_column")]}
    operations, client = plan([invalid, valid])

    assert operations[0].kind is OperationType.INDEX_COLUMN
    assert len(client.completions.calls) == 2
    retry_messages = client.completions.calls[1]["messages"]
    assert "Validation error" in retry_messages[-1]["content"]


def test_arithmetic_result_text_cannot_bypass_operation_validation():
    operation_with_result = operation_payload() | {
        "result": {"values": [100, 110, 120]},
        "arithmetic": "value / base * 100",
    }
    invalid = {
        "operations": [operation_with_result],
        "analysis": "I calculated the indexed values directly.",
    }
    client = FakeMIAClient([invalid, invalid])

    with pytest.raises(PlannerValidationError):
        OperationPlanner(client).plan("Index loans", "column_key=loans", 0)

    assert len(client.completions.calls) == 2


def test_native_strict_json_schema_path_is_used():
    _, client = plan([{"operations": [operation_payload()]}])
    call = client.completions.calls[0]

    assert call["model"] == "test-mia-model"
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert call["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "operation_plan",
            "schema": operation_plan_schema(),
            "strict": True,
        },
    }
    # One branch per operation kind, each pairing the kind with its own parameters. A
    # single shape with an untagged parameters union is satisfiable by any kind carrying
    # any kind's parameters, and the model duly emitted add_column with index_column's.
    item_schema = call["response_format"]["json_schema"]["schema"]["properties"]["operations"][
        "items"
    ]
    branches = item_schema["anyOf"]
    assert {branch["properties"]["kind"]["const"] for branch in branches} == {
        "add_column",
        "deflate_column",
        "index_column",
        "revert_to",
    }
    for branch in branches:
        assert set(branch["required"]) == {"kind", "parameters"}


def test_identifier_fields_are_narrowed_to_what_exists():
    """Under constrained decoding a free string field is satisfied by the shortest legal
    string - "series_reference": "b" - however firmly the prompt asks otherwise. The only
    reliable fix is to remove the choice."""
    schema = operation_plan_schema(
        series_references=["catalog:cpi"],
        column_keys=["cpi", "loans"],
        conventions=["cpi_base_period_constant_prices_v1"],
        base_dates=["2021-01-01"],
    )
    branches = {
        branch["properties"]["kind"]["const"]: branch["properties"]["parameters"]["properties"]
        for branch in schema["properties"]["operations"]["items"]["anyOf"]
    }
    assert branches["add_column"]["series_reference"] == {"enum": ["catalog:cpi"]}
    assert branches["deflate_column"]["deflator_column_key"] == {"enum": ["cpi", "loans"]}
    assert branches["deflate_column"]["convention_reference"] == {
        "enum": ["cpi_base_period_constant_prices_v1"]
    }
    assert branches["deflate_column"]["base_date"] == {"enum": ["2021-01-01"]}


def test_an_unconstrained_schema_leaves_identifiers_free():
    """The narrowing is opt-in: a caller with no vocabulary to offer still gets a usable
    schema rather than one that permits nothing."""
    branches = {
        branch["properties"]["kind"]["const"]: branch["properties"]["parameters"]["properties"]
        for branch in operation_plan_schema()["properties"]["operations"]["items"]["anyOf"]
    }
    assert "enum" not in branches["add_column"]["series_reference"]


def test_planner_never_executes_or_receives_a_frame():
    with patch("kkb_agent.agent.executor.OperationExecutor.execute") as execute:
        operations, _ = plan([{"operations": [operation_payload()]}])
    execute.assert_not_called()
    assert operations[0].kind is OperationType.INDEX_COLUMN


def test_an_operation_carrying_bookkeeping_is_refused_after_one_retry():
    """Versions and ids are assigned by the planner, so a model that sends its own is not
    following the contract - and its numbers would silently override ours."""
    invalid = {
        "operations": [
            {
                "kind": "index_column",
                "parameters": {"column_key": "loans", "base_date": "2025-01-01"},
                "source_version": 4,
            }
        ]
    }
    client = FakeMIAClient([invalid, invalid])

    with pytest.raises(PlannerValidationError, match="only kind and parameters"):
        OperationPlanner(client).plan("Index loans", "column_key=loans", 0)

    assert len(client.completions.calls) == 2


def test_versions_are_assigned_contiguously_from_the_current_version():
    """The model says what to do and in what order; numbering it is arithmetic."""
    payload = {
        "operations": [operation_payload("index_column"), operation_payload("deflate_column")]
    }
    operations, _ = plan([payload], current_version=7)

    assert [op.source_version for op in operations] == [7, 8]
    assert [op.resulting_version for op in operations] == [8, 9]
    assert len({op.operation_id for op in operations}) == 2


def test_provider_failure_is_explicit_and_not_retried():
    client = FakeMIAClient([RuntimeError("offline")])

    with pytest.raises(PlannerModelError, match="^MIA operation planning call failed$"):
        OperationPlanner(client).plan("Index loans", "column_key=loans", 0)

    assert len(client.completions.calls) == 1
