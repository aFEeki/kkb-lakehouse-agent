"""Production operation handlers and their domain errors."""

from kkb_agent.agent.handlers.index_column import (
    IndexBasePeriodMissingError,
    IndexBasePeriodNotFoundError,
    IndexBaseValueZeroError,
    IndexCalculationError,
    IndexColumnCollisionError,
    IndexColumnError,
    IndexColumnNotFoundError,
    IndexColumnTypeError,
    IndexSpineTypeError,
    index_column_handler,
    indexed_column_key,
)

__all__ = [
    "IndexBasePeriodMissingError",
    "IndexBasePeriodNotFoundError",
    "IndexBaseValueZeroError",
    "IndexCalculationError",
    "IndexColumnCollisionError",
    "IndexColumnError",
    "IndexColumnNotFoundError",
    "IndexColumnTypeError",
    "IndexSpineTypeError",
    "index_column_handler",
    "indexed_column_key",
]
