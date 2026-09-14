"""Explicit composition of available production operation handlers."""

from kkb_agent.agent.executor import OperationExecutor
from kkb_agent.agent.handlers import (
    SeriesSource,
    deflate_column_handler,
    index_column_handler,
    make_add_series_column_handler,
)
from kkb_agent.agent.history import FrameSnapshotHistory
from kkb_agent.frame import OperationType


def create_operation_executor(
    snapshot_history: FrameSnapshotHistory | None = None,
    series_source: SeriesSource | None = None,
) -> OperationExecutor:
    """Build an executor containing only currently implemented production handlers.

    add_column is registered only when a series source is supplied. Without one the
    executor cannot bring a number in from outside, and the executor's own error - "valid
    but has no handler" - says exactly that, which beats a handler that fails later with
    an empty catalog.
    """
    handlers = {
        OperationType.DEFLATE_COLUMN: deflate_column_handler,
        OperationType.INDEX_COLUMN: index_column_handler,
    }
    if series_source is not None:
        handlers[OperationType.ADD_COLUMN] = make_add_series_column_handler(series_source)
    return OperationExecutor(handlers, snapshot_history=snapshot_history or FrameSnapshotHistory())
