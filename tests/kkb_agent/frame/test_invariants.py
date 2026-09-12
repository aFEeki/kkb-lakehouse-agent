from types import SimpleNamespace

import pytest

from kkb_agent.frame import (
    ColumnAlignmentViolation,
    Spine,
    SpineViolation,
    assert_columns_aligned,
    assert_spine_intact,
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
