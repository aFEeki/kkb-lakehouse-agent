from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from kkb_agent.agent import (
    OperationExecutor,
    OperationHandlerError,
    OperationPostconditionError,
    OperationVersionError,
    UnimplementedOperationError,
    UnsupportedOperationError,
)
from kkb_agent.frame import AnalysisFrame, Operation, OperationType, Spine


def make_frame() -> AnalysisFrame:
    return AnalysisFrame(
        frame_id="frame-a",
        spine=Spine(values=[date(2025, 1, 1), date(2025, 2, 1)]),
    )


def make_operation(**changes) -> Operation:
    values = {
        "operation_id": "op-a",
        "kind": "add_column",
        "parameters": {"series_reference": "catalog:series-a", "column_key": "series-a"},
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "source_version": 0,
        "resulting_version": 1,
    }
    return Operation(**(values | changes))


def passthrough(frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
    return frame


def test_registered_operation_dispatches_and_commits_once():
    frame = make_frame()
    operation = make_operation()
    calls = []

    def handler(received_frame, received_operation):
        calls.append((received_frame, received_operation))
        return received_frame

    executor = OperationExecutor({OperationType.ADD_COLUMN: handler})
    result = executor.execute(frame, operation)

    assert calls == [(frame, operation)]
    assert result is not frame
    assert result.version == 1
    assert result.operations == (operation,)
    assert frame.version == 0
    assert frame.operations == ()
    assert executor.supported_operations == frozenset({OperationType.ADD_COLUMN})


def test_valid_but_unregistered_operation_fails_clearly():
    frame = make_frame()
    with pytest.raises(UnimplementedOperationError, match="valid but has no handler"):
        OperationExecutor().execute(frame, make_operation())
    assert frame.version == 0
    assert frame.operations == ()


def test_invalid_payload_is_rejected_by_operation_contract():
    with pytest.raises(ValidationError):
        Operation.model_validate(
            {
                **make_operation().model_dump(),
                "kind": "arbitrary_python",
                "parameters": {"code": "pass"},
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_version": 1, "resulting_version": 2},
        {"source_version": 2, "resulting_version": 3},
    ],
)
def test_source_version_mismatch_is_atomic(changes):
    frame = make_frame()
    with pytest.raises(OperationVersionError, match="frame is version 0"):
        OperationExecutor({OperationType.ADD_COLUMN: passthrough}).execute(
            frame, make_operation(**changes)
        )
    assert frame.version == 0
    assert frame.operations == ()


def test_handler_exception_is_atomic_and_preserves_cause():
    frame = make_frame()

    def fail(frame, operation):
        raise LookupError("missing test input")

    with pytest.raises(OperationHandlerError, match="add_column.*version 0") as caught:
        OperationExecutor({OperationType.ADD_COLUMN: fail}).execute(frame, make_operation())
    assert isinstance(caught.value.__cause__, LookupError)
    assert frame.version == 0
    assert frame.operations == ()


@pytest.mark.parametrize(
    "spine",
    [
        Spine(values=[date(2025, 1, 1)]),
        Spine(values=[date(2025, 2, 1), date(2025, 1, 1)]),
        Spine(values=[date(2025, 1, 1), date(2025, 3, 1)]),
        Spine(key="other", values=[date(2025, 1, 1), date(2025, 2, 1)]),
    ],
)
def test_changed_spine_is_rejected_atomically(spine):
    frame = make_frame()

    def change_spine(frame, operation):
        return AnalysisFrame(**(frame.model_dump() | {"spine": spine}))

    with pytest.raises(OperationPostconditionError, match="invalid candidate"):
        OperationExecutor({OperationType.ADD_COLUMN: change_spine}).execute(frame, make_operation())
    assert frame.version == 0
    assert frame.operations == ()


def test_handler_cannot_rewrite_history_or_commit_version():
    first = make_operation()
    frame = AnalysisFrame(frame_id="frame-a", version=1, spine=Spine(values=[]), operations=[first])
    requested = make_operation(operation_id="op-b", source_version=1, resulting_version=2)
    replacement = make_operation(operation_id="rewritten")

    def rewrite(frame, operation):
        return AnalysisFrame(
            frame_id=frame.frame_id,
            version=1,
            spine=frame.spine,
            operations=[replacement],
        )

    with pytest.raises(OperationPostconditionError, match="rewrote operation history"):
        OperationExecutor({OperationType.ADD_COLUMN: rewrite}).execute(frame, requested)

    def commit_early(frame, operation):
        return AnalysisFrame(
            **(frame.model_dump() | {"version": 2, "operations": [*frame.operations, operation]})
        )

    with pytest.raises(OperationPostconditionError, match="changed version before commit"):
        OperationExecutor({OperationType.ADD_COLUMN: commit_early}).execute(frame, requested)
    assert frame.version == 1
    assert frame.operations == (first,)


def test_duplicate_operation_cannot_be_appended():
    first = make_operation()
    frame = AnalysisFrame(frame_id="frame-a", version=1, spine=Spine(values=[]), operations=[first])
    duplicate = make_operation(source_version=1, resulting_version=2)
    with pytest.raises(OperationPostconditionError, match="invalid successor"):
        OperationExecutor({OperationType.ADD_COLUMN: passthrough}).execute(frame, duplicate)
    assert frame.operations == (first,)


@pytest.mark.parametrize("candidate", [None, {}, "frame"])
def test_handler_must_return_analysis_frame(candidate):
    frame = make_frame()
    with pytest.raises(OperationPostconditionError, match="did not return"):
        OperationExecutor({OperationType.ADD_COLUMN: lambda frame, operation: candidate}).execute(
            frame, make_operation()
        )
    assert frame.version == 0


@pytest.mark.parametrize("key", ["add_column", "os.system", object()])
def test_registry_rejects_arbitrary_keys(key):
    with pytest.raises(UnsupportedOperationError, match="not an OperationType"):
        OperationExecutor({key: passthrough})


def test_registry_is_copied_and_cannot_be_changed_after_construction():
    handlers = {OperationType.ADD_COLUMN: passthrough}
    executor = OperationExecutor(handlers)
    handlers.clear()
    assert executor.execute(make_frame(), make_operation()).version == 1


def test_executor_rejects_unvalidated_input():
    with pytest.raises(UnsupportedOperationError, match="Operation.model_validate"):
        OperationExecutor().execute(make_frame(), make_operation().model_dump())
    with pytest.raises(TypeError, match="AnalysisFrame"):
        OperationExecutor().execute({}, make_operation())


def test_non_callable_handler_is_rejected():
    with pytest.raises(TypeError, match="must be callable"):
        OperationExecutor({OperationType.ADD_COLUMN: "not-callable"})
