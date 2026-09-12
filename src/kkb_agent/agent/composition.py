"""Explicit composition of available production operation handlers."""

from kkb_agent.agent.executor import OperationExecutor
from kkb_agent.agent.handlers import deflate_column_handler, index_column_handler
from kkb_agent.frame import OperationType


def create_operation_executor() -> OperationExecutor:
    """Build an executor containing only currently implemented production handlers."""
    return OperationExecutor(
        {
            OperationType.DEFLATE_COLUMN: deflate_column_handler,
            OperationType.INDEX_COLUMN: index_column_handler,
        }
    )
