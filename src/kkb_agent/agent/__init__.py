"""Public deterministic agent execution contracts."""

from kkb_agent.agent.composition import create_operation_executor
from kkb_agent.agent.executor import (
    OperationExecutionError,
    OperationExecutor,
    OperationHandler,
    OperationHandlerError,
    OperationPostconditionError,
    OperationVersionError,
    UnimplementedOperationError,
    UnsupportedOperationError,
)
from kkb_agent.agent.findings import (
    DuplicateFindingError,
    FindingAlreadySupersededError,
    FindingEvidenceError,
    FindingNotFoundError,
    FindingServiceError,
    create_finding,
    revise_finding,
)
from kkb_agent.agent.history import (
    FrameSnapshot,
    FrameSnapshotHistory,
    SnapshotConflictError,
    SnapshotHistoryError,
    SnapshotLineageError,
    SnapshotNotFoundError,
)

__all__ = [
    "DuplicateFindingError",
    "FindingAlreadySupersededError",
    "FindingEvidenceError",
    "FindingNotFoundError",
    "FindingServiceError",
    "FrameSnapshot",
    "FrameSnapshotHistory",
    "OperationExecutionError",
    "OperationExecutor",
    "OperationHandler",
    "OperationHandlerError",
    "OperationPostconditionError",
    "OperationVersionError",
    "SnapshotConflictError",
    "SnapshotHistoryError",
    "SnapshotLineageError",
    "SnapshotNotFoundError",
    "UnimplementedOperationError",
    "UnsupportedOperationError",
    "create_finding",
    "create_operation_executor",
    "revise_finding",
]
