"""Deterministic dispatch and transition validation for AnalysisFrame operations."""

from collections.abc import Callable, Mapping
from types import MappingProxyType

from pydantic import ValidationError

from kkb_agent.agent.history import FrameSnapshotHistory
from kkb_agent.frame import AnalysisFrame, Operation, OperationType, assert_spine_intact

OperationHandler = Callable[[AnalysisFrame, Operation], AnalysisFrame]


class OperationExecutionError(RuntimeError):
    """Base error for a failed, atomic operation attempt."""


class UnsupportedOperationError(OperationExecutionError):
    """The executor received input outside its validated operation contract."""


class UnimplementedOperationError(OperationExecutionError):
    """A valid vocabulary member has no registered business handler."""


class OperationVersionError(OperationExecutionError):
    """The operation targets a frame version other than the current one."""


class OperationHandlerError(OperationExecutionError):
    """A registered handler failed before producing a candidate snapshot."""


class OperationPostconditionError(OperationExecutionError):
    """A handler result or final successor violated the execution boundary."""


class OperationExecutor:
    """Dispatch validated operations through an explicit, immutable registry."""

    def __init__(
        self,
        handlers: Mapping[OperationType, OperationHandler] | None = None,
        *,
        snapshot_history: FrameSnapshotHistory | None = None,
    ):
        checked: dict[OperationType, OperationHandler] = {}
        for kind, handler in (handlers or {}).items():
            if type(kind) is not OperationType:
                raise UnsupportedOperationError(
                    f"Registry key {kind!r} is not an OperationType member"
                )
            if not callable(handler):
                raise TypeError(f"Handler for {kind.value!r} must be callable")
            checked[kind] = handler
        self._handlers = MappingProxyType(checked)
        self._snapshot_history = snapshot_history

    @property
    def supported_operations(self) -> frozenset[OperationType]:
        supported = set(self._handlers)
        if self._snapshot_history is not None:
            supported.add(OperationType.REVERT_TO)
        return frozenset(supported)

    @property
    def snapshot_history(self) -> FrameSnapshotHistory | None:
        return self._snapshot_history

    def execute(self, frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
        if not isinstance(frame, AnalysisFrame):
            raise TypeError("frame must be a validated AnalysisFrame")
        if not isinstance(operation, Operation):
            raise UnsupportedOperationError(
                "operation must be validated with Operation.model_validate before execution"
            )

        kind = operation.kind
        if operation.source_version != frame.version:
            raise OperationVersionError(
                f"Operation {kind.value!r} targets version {operation.source_version}; "
                f"frame is version {frame.version}"
            )
        if operation.resulting_version != frame.version + 1:
            raise OperationVersionError(
                f"Operation {kind.value!r} results in version {operation.resulting_version}; "
                f"expected {frame.version + 1}"
            )

        if kind is OperationType.REVERT_TO and self._snapshot_history is not None:
            return self._execute_revert(frame, operation)

        handler = self._handlers.get(kind)
        if handler is None:
            raise UnimplementedOperationError(
                f"Operation {kind.value!r} is valid but has no handler at frame version "
                f"{frame.version}"
            )

        try:
            candidate = handler(frame, operation)
        except OperationExecutionError:
            raise
        except Exception as exc:
            raise OperationHandlerError(
                f"Handler for {kind.value!r} failed at frame version {frame.version}: {exc}"
            ) from exc

        self._validate_handler_candidate(frame, operation, candidate)
        try:
            successor = AnalysisFrame.model_validate(
                {
                    **candidate.model_dump(),
                    "version": operation.resulting_version,
                    "operations": (*frame.operations, operation),
                }
            )
            successor.assert_successor_of(frame)
        except (ValidationError, ValueError) as exc:
            raise OperationPostconditionError(
                f"Operation {kind.value!r} produced an invalid successor from frame version "
                f"{frame.version}: {exc}"
            ) from exc

        if successor.operations != (*frame.operations, operation):
            raise OperationPostconditionError(
                f"Operation {kind.value!r} was not appended exactly once at frame version "
                f"{frame.version}"
            )
        if self._snapshot_history is not None:
            self._snapshot_history.retain_transition(frame, successor)
        return successor

    def _execute_revert(self, frame: AnalysisFrame, operation: Operation) -> AnalysisFrame:
        history = self._snapshot_history
        if history is None:  # pragma: no cover - guarded by execute
            raise UnimplementedOperationError("revert_to requires snapshot history")
        target = history.resolve(frame, operation.parameters.target_version)
        try:
            successor = AnalysisFrame.model_validate(
                {
                    **frame.model_dump(),
                    "version": operation.resulting_version,
                    "spine": target.spine,
                    "columns": target.columns,
                    "charts": target.charts,
                    "findings": frame.findings,
                    "operations": (*frame.operations, operation),
                }
            )
            successor.assert_successor_of(frame)
            assert_spine_intact(frame.spine, successor.spine)
        except (ValidationError, ValueError) as exc:
            raise OperationPostconditionError(
                f"Operation {operation.kind.value!r} produced an invalid restored successor "
                f"from frame version {frame.version}: {exc}"
            ) from exc
        if successor.operations != (*frame.operations, operation):
            raise OperationPostconditionError(
                f"Operation {operation.kind.value!r} was not appended exactly once at frame "
                f"version {frame.version}"
            )
        history.retain_transition(frame, successor)
        return successor

    @staticmethod
    def _validate_handler_candidate(
        frame: AnalysisFrame, operation: Operation, candidate: object
    ) -> None:
        kind = operation.kind.value
        if not isinstance(candidate, AnalysisFrame):
            raise OperationPostconditionError(
                f"Handler for {kind!r} did not return an AnalysisFrame at frame version "
                f"{frame.version}"
            )
        try:
            candidate = AnalysisFrame.model_validate(candidate)
            assert_spine_intact(frame.spine, candidate.spine)
        except (ValidationError, ValueError) as exc:
            raise OperationPostconditionError(
                f"Handler for {kind!r} produced an invalid candidate at frame version "
                f"{frame.version}: {exc}"
            ) from exc
        if candidate.frame_id != frame.frame_id:
            raise OperationPostconditionError(
                f"Handler for {kind!r} changed frame ID at frame version {frame.version}"
            )
        if candidate.version != frame.version:
            raise OperationPostconditionError(
                f"Handler for {kind!r} changed version before commit at frame version "
                f"{frame.version}"
            )
        if candidate.operations != frame.operations:
            raise OperationPostconditionError(
                f"Handler for {kind!r} rewrote operation history at frame version {frame.version}"
            )
