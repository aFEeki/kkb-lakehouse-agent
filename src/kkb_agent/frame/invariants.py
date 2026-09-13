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

    def model_dump_json(self) -> str: ...


def assert_spine_intact(before: _Spine, after: _Spine) -> None:
    try:
        before_identity = (before.key, before.kind, before.values)
        after_identity = (after.key, after.kind, after.values)
        after_is_unique = len(after.values) == len(set(after.values))
    except (AttributeError, TypeError) as exc:
        raise SpineViolation("Spine cannot be validated as an ordered unique identity") from exc
    if not after_is_unique:
        raise SpineViolation("Spine key values must remain unique")
    if before_identity != after_identity:
        raise SpineViolation("Spine identity, type, values and ordering must remain unchanged")


def assert_columns_aligned(spine: _Spine, columns: Iterable[_Column]) -> None:
    for column in columns:
        if len(column.values) != len(spine.values):
            raise ColumnAlignmentViolation(
                f"Column {column.key!r} has {len(column.values)} values; "
                f"spine has {len(spine.values)} rows"
            )


def assert_existing_columns_intact(before: Iterable[_Column], after: Iterable[_Column]) -> None:
    """Require every existing column to retain its order and serialized bytes."""

    try:
        before_columns = tuple(before)
        after_columns = tuple(after)
    except TypeError as exc:
        raise SpineViolation("Existing columns cannot be inspected safely") from exc
    if len(after_columns) < len(before_columns):
        raise SpineViolation("A column-adding operation removed an existing column")

    for position, (original, candidate) in enumerate(
        zip(before_columns, after_columns, strict=False)
    ):
        try:
            original_bytes = original.model_dump_json().encode("utf-8")
            candidate_bytes = candidate.model_dump_json().encode("utf-8")
            original_key = original.key
            candidate_key = candidate.key
        except (AttributeError, TypeError, ValueError) as exc:
            raise SpineViolation(
                f"Existing column at position {position} cannot be serialized safely"
            ) from exc
        if original_key != candidate_key or original_bytes != candidate_bytes:
            raise SpineViolation(
                f"Existing column {original_key!r} changed content or order at position {position}"
            )
