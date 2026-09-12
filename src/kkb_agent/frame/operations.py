"""Closed operation records, not executable commands or an executor."""

from datetime import date
from enum import StrEnum

from pydantic import model_validator

from kkb_agent.frame._base import Contract, Identifier, Timestamp, Version


class OperationType(StrEnum):
    ADD_COLUMN = "add_column"
    DEFLATE_COLUMN = "deflate_column"
    INDEX_COLUMN = "index_column"
    REVERT_TO = "revert_to"


class AddColumnParameters(Contract):
    series_reference: Identifier
    column_key: Identifier


class DeflateColumnParameters(Contract):
    column_key: Identifier
    deflator_column_key: Identifier
    # Convention is deliberately explicit; no default index or price basis is chosen here.
    base_date: date
    convention_reference: Identifier


class IndexColumnParameters(Contract):
    """Rebase to 100 at base_date; the future executor retains the original column."""

    column_key: Identifier
    base_date: date


class RevertToParameters(Contract):
    target_version: Version


class Operation(Contract):
    operation_id: Identifier
    kind: OperationType
    parameters: (
        AddColumnParameters | DeflateColumnParameters | IndexColumnParameters | RevertToParameters
    )
    timestamp: Timestamp
    source_version: Version
    resulting_version: Version

    @model_validator(mode="after")
    def validate_record(self):
        expected = {
            OperationType.ADD_COLUMN: AddColumnParameters,
            OperationType.DEFLATE_COLUMN: DeflateColumnParameters,
            OperationType.INDEX_COLUMN: IndexColumnParameters,
            OperationType.REVERT_TO: RevertToParameters,
        }[self.kind]
        if not isinstance(self.parameters, expected):
            raise ValueError("Operation kind and parameter schema do not match")
        if self.resulting_version != self.source_version + 1:
            raise ValueError("An operation must advance the version by exactly one")
        if isinstance(self.parameters, RevertToParameters):
            if self.parameters.target_version >= self.source_version:
                raise ValueError("Revert target must precede the source version")
        if isinstance(self.parameters, DeflateColumnParameters):
            if self.parameters.column_key == self.parameters.deflator_column_key:
                raise ValueError("Target and deflator columns must differ")
        return self
