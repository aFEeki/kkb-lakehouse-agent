"""Append-only creation and revision of findings on an AnalysisFrame."""

from collections.abc import Iterable

from kkb_agent.frame import AnalysisFrame, Finding, SpineRange


class FindingServiceError(ValueError):
    """Base error for a rejected finding update."""


class DuplicateFindingError(FindingServiceError):
    """A finding ID is already present in the frame history."""


class FindingNotFoundError(FindingServiceError):
    """The finding requested for revision does not exist."""


class FindingAlreadySupersededError(FindingServiceError):
    """The requested finding already has a direct revision."""


class FindingEvidenceError(FindingServiceError):
    """Supporting columns or range do not exist in the current frame."""


def _validate_new_evidence(
    frame: AnalysisFrame,
    finding_id: str,
    supporting_column_keys: tuple[str, ...],
    spine_range: SpineRange | None,
) -> None:
    if any(finding.finding_id == finding_id for finding in frame.findings):
        raise DuplicateFindingError(f"Finding {finding_id!r} already exists")

    available = {column.key for column in frame.columns}
    missing = [key for key in supporting_column_keys if key not in available]
    if missing:
        raise FindingEvidenceError(
            f"Finding {finding_id!r} references unknown current columns: {missing!r}"
        )
    if spine_range is not None and spine_range.stop > len(frame.spine.values):
        raise FindingEvidenceError(
            f"Finding {finding_id!r} range ends at {spine_range.stop}; "
            f"current spine has {len(frame.spine.values)} rows"
        )


def _append_finding(frame: AnalysisFrame, finding: Finding) -> AnalysisFrame:
    return AnalysisFrame.model_validate(
        {**frame.model_dump(), "findings": (*frame.findings, finding)}
    )


def create_finding(
    frame: AnalysisFrame,
    *,
    finding_id: str,
    statement: str,
    supporting_column_keys: Iterable[str],
    producing_tool: str,
    spine_range: SpineRange | None = None,
    confidence: float | None = None,
    caveats: Iterable[str] = (),
) -> AnalysisFrame:
    """Append a current-version finding without changing analytical frame state."""
    column_keys = tuple(supporting_column_keys)
    _validate_new_evidence(frame, finding_id, column_keys, spine_range)
    finding = Finding(
        finding_id=finding_id,
        statement=statement,
        frame_version=frame.version,
        supporting_column_keys=column_keys,
        spine_range=spine_range,
        producing_tool=producing_tool,
        confidence=confidence,
        caveats=tuple(caveats),
    )
    return _append_finding(frame, finding)


def revise_finding(
    frame: AnalysisFrame,
    superseded_finding_id: str,
    *,
    revision_id: str,
    statement: str,
    supporting_column_keys: Iterable[str],
    producing_tool: str,
    spine_range: SpineRange | None = None,
    confidence: float | None = None,
    caveats: Iterable[str] = (),
) -> AnalysisFrame:
    """Append a revision that points to one existing, unsuperseded finding."""
    if not any(finding.finding_id == superseded_finding_id for finding in frame.findings):
        raise FindingNotFoundError(f"Finding {superseded_finding_id!r} does not exist")

    direct_revision = next(
        (finding for finding in frame.findings if finding.supersedes == superseded_finding_id),
        None,
    )
    if direct_revision is not None:
        raise FindingAlreadySupersededError(
            f"Finding {superseded_finding_id!r} is already superseded by "
            f"{direct_revision.finding_id!r}; revise the latest finding instead"
        )

    column_keys = tuple(supporting_column_keys)
    _validate_new_evidence(frame, revision_id, column_keys, spine_range)
    revision = Finding(
        finding_id=revision_id,
        statement=statement,
        frame_version=frame.version,
        supporting_column_keys=column_keys,
        spine_range=spine_range,
        producing_tool=producing_tool,
        supersedes=superseded_finding_id,
        confidence=confidence,
        caveats=tuple(caveats),
    )
    return _append_finding(frame, revision)
