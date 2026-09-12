from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from kkb_agent.frame import (
    AnalysisFrame,
    ChartSpec,
    Column,
    Finding,
    Lineage,
    MetadataEntry,
    ParentLineage,
    SourceReference,
    Spine,
    SpineRange,
    Transformation,
)


@pytest.fixture
def column():
    return Column(
        key="series_a",
        label="Series A",
        dtype="number",
        values=[10, None],
        origin="source",
        lineage=Lineage(
            sources=[
                SourceReference(
                    source_type="file",
                    reference="book.xlsx#Sheet1!A1:B3",
                    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                    raw_sha256="a" * 64,
                    metadata=[MetadataEntry(key="file.sheet", value="Sheet1")],
                )
            ]
        ),
    )


@pytest.fixture
def frame(column):
    return AnalysisFrame(
        frame_id="analysis-a",
        spine=Spine(values=[date(2025, 1, 1), date(2025, 2, 1)]),
        columns=[column],
    )


def test_valid_frame_preserves_missing_and_json(frame):
    assert frame.columns[0].values == (10, None)
    assert type(frame.columns[0].values[0]) is int
    assert frame.columns[0].missing_count == 1
    assert frame.columns[0].unit is None
    encoded = frame.model_dump_json()
    assert "null" in encoded
    restored = AnalysisFrame.model_validate_json(encoded)
    assert restored == frame
    assert restored.model_dump_json() == encoded
    assert AnalysisFrame.model_json_schema()["title"] == "AnalysisFrame"


def test_insertion_order_and_duplicate_keys(frame, column):
    second = Column(**{**column.model_dump(), "key": "z"})
    result = AnalysisFrame(frame_id="f", spine=frame.spine, columns=[second, column])
    assert [item.key for item in result.columns] == ["z", "series_a"]
    with pytest.raises(ValidationError, match="Duplicate column"):
        AnalysisFrame(frame_id="f", spine=frame.spine, columns=[column, column])


@pytest.mark.parametrize("count", [1, 3])
def test_frame_rejects_misaligned_column(frame, column, count):
    bad = Column(**{**column.model_dump(), "values": [1] * count})
    with pytest.raises(ValidationError, match="rows"):
        AnalysisFrame(frame_id="f", spine=frame.spine, columns=[bad])


@pytest.mark.parametrize("version", [-1, True, "1", 0.5, 2])
def test_invalid_version_or_missing_history(frame, version):
    with pytest.raises(ValidationError):
        AnalysisFrame(**{**frame.model_dump(), "version": version})


def test_spine_preserves_order_and_rejects_duplicates():
    dates = [date(2025, 2, 1), date(2025, 1, 1)]
    assert Spine(values=dates).values == tuple(dates)
    with pytest.raises(ValidationError, match="Duplicate"):
        Spine(values=[dates[0], dates[0]])


@pytest.mark.parametrize("values", [[0], [None], [datetime(2025, 1, 1)], ["2025-01-01T00:00:00"]])
def test_date_spine_rejects_ambiguous_values(values):
    with pytest.raises(ValidationError):
        Spine(values=values)


def test_timestamp_spine_normalizes_and_roundtrips():
    spine = Spine(kind="datetime", values=["2025-01-01T03:00:00+03:00"])
    assert spine.values == (datetime(2025, 1, 1, tzinfo=UTC),)
    assert Spine.model_validate_json(spine.model_dump_json()) == spine
    with pytest.raises(ValidationError, match="Duplicate"):
        Spine(kind="datetime", values=["2025-01-01T03:00:00+03:00", "2025-01-01T00:00:00Z"])
    with pytest.raises(ValidationError):
        Spine(kind="datetime", values=["2025-01-01T00:00:00"])


@pytest.mark.parametrize(
    "dtype,values",
    [
        ("number", [True]),
        ("integer", [1.2]),
        ("number", ["12"]),
        ("boolean", [1]),
        ("string", [1]),
        ("number", [float("nan")]),
        ("number", [float("inf")]),
    ],
)
def test_column_rejects_coercion_and_nonfinite_numbers(column, dtype, values):
    with pytest.raises(ValidationError):
        Column(**{**column.model_dump(), "dtype": dtype, "values": values})


def test_recursive_derived_lineage_roundtrip(column):
    lineage = Lineage(
        parents=[
            ParentLineage(
                frame_id="f", frame_version=0, column_key=column.key, lineage=column.lineage
            )
        ],
        transformations=[
            Transformation(
                name="scale",
                implementation_version="1",
                parameters=[MetadataEntry(key="factor", value=2)],
            )
        ],
    )
    derived = Column(
        key="b", label="B", dtype="number", origin="derived", values=[20, None], lineage=lineage
    )
    restored = Column.model_validate_json(derived.model_dump_json())
    assert restored == derived
    assert restored.lineage.parents[0].lineage.sources[0].raw_sha256 == "a" * 64
    with pytest.raises(ValidationError):
        Column(**{**column.model_dump(), "origin": "derived"})
    with pytest.raises(ValidationError):
        Lineage()


def test_finding_and_chart_references(frame):
    finding = Finding(
        finding_id="finding-a",
        statement="Series is incomplete.",
        frame_version=0,
        supporting_column_keys=["series_a"],
        spine_range=SpineRange(start=0, stop=2),
        producing_tool="quality",
        confidence=0.9,
        caveats=["One missing value"],
    )
    chart = ChartSpec(
        chart_id="chart-a", chart_type="line", spine_key="time", column_keys=["series_a"]
    )
    result = AnalysisFrame(**{**frame.model_dump(), "findings": [finding], "charts": [chart]})
    assert AnalysisFrame.model_validate_json(result.model_dump_json()) == result
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    for changes in [
        {"supporting_column_keys": ["absent"]},
        {"frame_version": 1},
        {"spine_range": {"start": 0, "stop": 3}},
        {"supersedes": "absent"},
    ]:
        bad = Finding(**{**finding.model_dump(), **changes})
        with pytest.raises(ValidationError):
            AnalysisFrame(**{**frame.model_dump(), "findings": [bad]})
    with pytest.raises(ValidationError):
        AnalysisFrame(
            **{
                **frame.model_dump(),
                "charts": [
                    {**chart.model_dump(), "column_keys": ["absent"]},
                ],
            }
        )


def test_frozen_and_defensive_sequences(frame, column):
    source_values = [1, None]
    copied = Column(**{**column.model_dump(), "values": source_values})
    source_values[0] = 999
    assert copied.values == (1, None)
    with pytest.raises(ValidationError):
        frame.spine.values = ()
    with pytest.raises(TypeError):
        frame.columns[0].values[0] = 999
    with pytest.raises(ValidationError):
        frame.columns[0].lineage.sources[0].metadata[0].value = "changed"


def test_empty_frame():
    frame = AnalysisFrame(frame_id="empty", spine=Spine(values=[]))
    assert frame.columns == ()
    assert AnalysisFrame.model_validate_json(frame.model_dump_json()) == frame


def test_unknown_fields_rejected(frame):
    with pytest.raises(ValidationError):
        AnalysisFrame(**frame.model_dump(), execute_python="print(1)")
