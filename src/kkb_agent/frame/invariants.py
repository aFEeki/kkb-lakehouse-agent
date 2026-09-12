"""Structural guards; never repair or reshape input."""

from collections.abc import Iterable
from typing import Protocol


class SpineViolation(ValueError):
    """The existing row identity was changed."""


class ColumnAlignmentViolation(ValueError):
    """A column's cardinality differs from the spine."""


class _Spine(Protocol):
    key: str
    kind: str
    values: tuple


class _Column(Protocol):
    key: str
    values: tuple


def assert_spine_intact(before: _Spine, after: _Spine) -> None:
    if (before.key, before.kind, before.values) != (after.key, after.kind, after.values):
        raise SpineViolation("Spine identity, type, values and ordering must remain unchanged")


def assert_columns_aligned(spine: _Spine, columns: Iterable[_Column]) -> None:
    for column in columns:
        if len(column.values) != len(spine.values):
            raise ColumnAlignmentViolation(
                f"Column {column.key!r} has {len(column.values)} values; "
                f"spine has {len(spine.values)} rows"
            )
