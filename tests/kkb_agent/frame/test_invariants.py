from types import SimpleNamespace

import pytest

from kkb_agent.frame import (
    Column,
    ColumnAlignmentViolation,
    Lineage,
    MetadataEntry,
    SourceReference,
    Spine,
    SpineViolation,
    Unit,
    assert_columns_aligned,
    assert_existing_columns_intact,
    assert_spine_intact,
)


def make_column(key="a", values=(1.0, 2.0), *, source_metadata=()):
    return Column(
        key=key,
        label=f"Column {key}",
        dtype="number",
        values=values,
        measure_type="stock",
        unit=Unit(symbol="TRY", scale=1_000_000.0, description="million TRY"),
        origin="source",
        lineage=Lineage(
            sources=[
                SourceReference(
                    source_type="evds",
                    reference=f"TP.{key.upper()}",
                    metadata=source_metadata,
                )
            ]
        ),
    )


@pytest.mark.parametrize(
    "values",
    [
        ["2025-01-01"],
        ["2025-01-01", "2025-02-01", "2025-03-01"],
        ["2025-02-01", "2025-01-01"],
        ["2025-01-01", "2025-02-02"],
    ],
)
def test_spine_change_detected(values):
    before = Spine(values=["2025-01-01", "2025-02-01"])
    with pytest.raises(SpineViolation):
        assert_spine_intact(before, Spine(values=values))


def test_identical_spine_and_changed_label():
    before = Spine(values=["2025-01-01"])
    assert_spine_intact(before, Spine(values=["2025-01-01"], label="Display only"))
    with pytest.raises(SpineViolation):
        assert_spine_intact(before, Spine(key="other", values=["2025-01-01"]))


@pytest.mark.parametrize("length", [0, 1, 3])
def test_column_alignment_violation(length):
    spine = Spine(values=["2025-01-01", "2025-02-01"])
    with pytest.raises(ColumnAlignmentViolation):
        assert_columns_aligned(spine, [SimpleNamespace(key="a", values=(None,) * length)])


def test_aligned_columns_and_empty_cases():
    spine = Spine(values=["2025-01-01", "2025-02-01"])
    assert_columns_aligned(spine, [SimpleNamespace(key="a", values=(1, None))])
    assert_columns_aligned(spine, [])
    assert_columns_aligned(Spine(values=[]), [])
    assert_columns_aligned(Spine(values=[]), [SimpleNamespace(key="a", values=())])


def test_existing_columns_allow_an_append_without_requiring_one():
    existing = make_column()
    appended = make_column("b")

    assert_existing_columns_intact([existing], [existing])
    assert_existing_columns_intact([existing], [existing, appended])


@pytest.mark.parametrize(
    "candidate",
    [
        make_column(values=(99.0, 2.0)),
        make_column(source_metadata=[MetadataEntry(key="revision", value="changed")]),
    ],
    ids=["value", "lineage-metadata"],
)
def test_existing_column_serialized_content_must_be_byte_identical(candidate):
    with pytest.raises(SpineViolation, match="changed content"):
        assert_existing_columns_intact([make_column()], [candidate])


def test_existing_column_order_and_presence_are_preserved():
    first = make_column("a")
    second = make_column("b")

    with pytest.raises(SpineViolation, match="changed content or order"):
        assert_existing_columns_intact([first, second], [second, first])
    with pytest.raises(SpineViolation, match="removed"):
        assert_existing_columns_intact([first, second], [first])


def test_malformed_existing_column_fails_as_spine_violation():
    with pytest.raises(SpineViolation, match="cannot be serialized safely"):
        assert_existing_columns_intact([make_column()], [object()])
