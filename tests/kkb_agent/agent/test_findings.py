from datetime import date

import pytest
from pydantic import ValidationError

from kkb_agent.agent import (
    DuplicateFindingError,
    FindingAlreadySupersededError,
    FindingEvidenceError,
    FindingNotFoundError,
    create_finding,
    revise_finding,
)
from kkb_agent.frame import (
    AnalysisFrame,
    ChartSpec,
    Column,
    Lineage,
    SourceReference,
    Spine,
    SpineRange,
)


def column(key: str) -> Column:
    return Column(
        key=key,
        label=key.title(),
        dtype="number",
        values=(1, 2, 3),
        origin="source",
        lineage=Lineage(
            sources=[SourceReference(source_type="fixture", reference=f"fixture:{key}")]
        ),
    )


def frame() -> AnalysisFrame:
    return AnalysisFrame(
        frame_id="frame-findings",
        spine=Spine(values=[date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)]),
        columns=[column("loans"), column("rates")],
        charts=[
            ChartSpec(
                chart_id="chart-a",
                chart_type="line",
                spine_key="time",
                column_keys=["loans", "rates"],
            )
        ],
    )


def add_initial(source: AnalysisFrame) -> AnalysisFrame:
    return create_finding(
        source,
        finding_id="finding-v1",
        statement="Loan growth coincided with lower rates.",
        supporting_column_keys=["loans", "rates"],
        spine_range=SpineRange(start=0, stop=3),
        producing_tool="change_detection",
        confidence=0.7,
        caveats=["Association does not establish causality."],
    )


def test_create_finding_records_evidence_and_preserves_frame_state():
    source = frame()
    result = add_initial(source)
    finding = result.findings[-1]

    assert finding.finding_id == "finding-v1"
    assert finding.frame_version == source.version
    assert finding.supporting_column_keys == ("loans", "rates")
    assert finding.spine_range == SpineRange(start=0, stop=3)
    assert finding.producing_tool == "change_detection"
    assert finding.caveats == ("Association does not establish causality.",)
    assert result.frame_id == source.frame_id
    assert result.version == source.version
    assert result.spine == source.spine
    assert result.columns == source.columns
    assert result.charts == source.charts
    assert result.operations == source.operations
    assert source.findings == ()


def test_revision_appends_and_keeps_original_unchanged():
    initial = add_initial(frame())
    original = initial.findings[0]

    revised = revise_finding(
        initial,
        "finding-v1",
        revision_id="finding-v2",
        statement="Real loan growth was flat after CPI adjustment.",
        supporting_column_keys=["loans"],
        spine_range=SpineRange(start=1, stop=3),
        producing_tool="deflate_column",
        confidence=0.9,
        caveats=["Uses headline CPI."],
    )

    assert revised.findings == (original, revised.findings[1])
    assert revised.findings[0] == original
    assert revised.findings[1].supersedes == "finding-v1"
    assert revised.findings[1].statement.startswith("Real loan growth")
    assert initial.findings == (original,)
    assert revised.spine == initial.spine
    assert revised.columns == initial.columns
    assert revised.charts == initial.charts
    assert revised.version == initial.version
    assert revised.operations == initial.operations


def test_repeated_revisions_preserve_full_reasoning_chain():
    first = add_initial(frame())
    second = revise_finding(
        first,
        "finding-v1",
        revision_id="finding-v2",
        statement="Second interpretation",
        supporting_column_keys=["loans"],
        producing_tool="deflate_column",
        caveats=["Second-stage caveat"],
    )
    third = revise_finding(
        second,
        "finding-v2",
        revision_id="finding-v3",
        statement="Third interpretation",
        supporting_column_keys=["loans", "rates"],
        producing_tool="causality",
        caveats=["Third-stage caveat"],
    )

    assert [finding.finding_id for finding in third.findings] == [
        "finding-v1",
        "finding-v2",
        "finding-v3",
    ]
    assert [finding.supersedes for finding in third.findings] == [
        None,
        "finding-v1",
        "finding-v2",
    ]
    assert [finding.statement for finding in third.findings] == [
        "Loan growth coincided with lower rates.",
        "Second interpretation",
        "Third interpretation",
    ]
    assert third.findings[0].caveats == ("Association does not establish causality.",)


def test_missing_or_already_superseded_revision_fails_atomically():
    initial = add_initial(frame())
    with pytest.raises(FindingNotFoundError, match="does not exist"):
        revise_finding(
            initial,
            "absent",
            revision_id="finding-v2",
            statement="Revision",
            supporting_column_keys=["loans"],
            producing_tool="test",
        )

    revised = revise_finding(
        initial,
        "finding-v1",
        revision_id="finding-v2",
        statement="Revision",
        supporting_column_keys=["loans"],
        producing_tool="test",
    )
    with pytest.raises(FindingAlreadySupersededError, match="finding-v2"):
        revise_finding(
            revised,
            "finding-v1",
            revision_id="finding-v3",
            statement="Branching revision",
            supporting_column_keys=["loans"],
            producing_tool="test",
        )
    assert initial.findings[0].supersedes is None
    assert len(initial.findings) == 1
    assert len(revised.findings) == 2


def test_duplicate_finding_id_fails_atomically():
    initial = add_initial(frame())
    with pytest.raises(DuplicateFindingError, match="already exists"):
        create_finding(
            initial,
            finding_id="finding-v1",
            statement="Duplicate",
            supporting_column_keys=["loans"],
            producing_tool="test",
        )
    assert len(initial.findings) == 1


@pytest.mark.parametrize("column_keys", [["missing"], ["loans", "missing"]])
def test_unknown_supporting_columns_fail_clearly(column_keys):
    source = frame()
    with pytest.raises(FindingEvidenceError, match="unknown current columns"):
        create_finding(
            source,
            finding_id="finding-bad",
            statement="Bad evidence",
            supporting_column_keys=column_keys,
            producing_tool="test",
        )
    assert source.findings == ()


def test_out_of_bounds_range_fails_clearly_and_empty_range_uses_contract():
    source = frame()
    with pytest.raises(FindingEvidenceError, match="current spine has 3 rows"):
        create_finding(
            source,
            finding_id="finding-bad",
            statement="Bad range",
            supporting_column_keys=["loans"],
            spine_range=SpineRange(start=0, stop=4),
            producing_tool="test",
        )
    with pytest.raises(ValidationError):
        SpineRange(start=1, stop=1)
    assert source.findings == ()
