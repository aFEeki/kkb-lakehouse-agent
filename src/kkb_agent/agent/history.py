"""Instance-scoped retention of immutable analytical frame snapshots."""

from dataclasses import dataclass

from kkb_agent.frame import AnalysisFrame, ChartSpec, Column, Operation, Spine


class SnapshotHistoryError(RuntimeError):
    """Base error for invalid or unavailable snapshot history."""


class SnapshotNotFoundError(SnapshotHistoryError):
    """The requested historical frame version was not retained."""


class SnapshotConflictError(SnapshotHistoryError):
    """A frame version conflicts with an already retained analytical snapshot."""


class SnapshotLineageError(SnapshotHistoryError):
    """A retained target does not belong to the current frame history."""


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """Immutable analytical state and operation prefix at one frame version."""

    frame_id: str
    version: int
    spine: Spine
    columns: tuple[Column, ...]
    charts: tuple[ChartSpec, ...]
    operations: tuple[Operation, ...]

    @classmethod
    def from_frame(cls, frame: AnalysisFrame) -> "FrameSnapshot":
        return cls(
            frame_id=frame.frame_id,
            version=frame.version,
            spine=frame.spine,
            columns=frame.columns,
            charts=frame.charts,
            operations=frame.operations,
        )


class FrameSnapshotHistory:
    """Retain versioned analytical state without replay or hidden persistence."""

    def __init__(self) -> None:
        self._snapshots: dict[str, dict[int, FrameSnapshot]] = {}

    def retain(self, frame: AnalysisFrame) -> None:
        """Retain one immutable snapshot, rejecting a conflicting version."""
        snapshot = FrameSnapshot.from_frame(frame)
        versions = self._snapshots.setdefault(frame.frame_id, {})
        existing = versions.get(frame.version)
        if existing is not None and existing != snapshot:
            raise SnapshotConflictError(
                f"Frame {frame.frame_id!r} version {frame.version} conflicts with retained state"
            )
        versions[frame.version] = snapshot

    def retain_transition(self, before: AnalysisFrame, after: AnalysisFrame) -> None:
        """Atomically validate and retain both ends of a successful transition."""
        if before.frame_id != after.frame_id or after.version != before.version + 1:
            raise SnapshotLineageError(
                "A retained transition must advance one frame by one version"
            )

        proposed = (FrameSnapshot.from_frame(before), FrameSnapshot.from_frame(after))
        versions = self._snapshots.get(before.frame_id, {})
        for snapshot in proposed:
            existing = versions.get(snapshot.version)
            if existing is not None and existing != snapshot:
                raise SnapshotConflictError(
                    f"Frame {snapshot.frame_id!r} version {snapshot.version} conflicts with "
                    "retained state"
                )

        committed = self._snapshots.setdefault(before.frame_id, {})
        for snapshot in proposed:
            committed[snapshot.version] = snapshot

    def resolve(self, current: AnalysisFrame, target_version: int) -> FrameSnapshot:
        """Return a retained target proven to share the current operation prefix."""
        versions = self._snapshots.get(current.frame_id)
        if versions is None or target_version not in versions:
            raise SnapshotNotFoundError(
                f"Frame {current.frame_id!r} version {target_version} was not retained"
            )
        current_snapshot = FrameSnapshot.from_frame(current)
        retained_current = versions.get(current.version)
        if retained_current is None:
            raise SnapshotNotFoundError(
                f"Current frame {current.frame_id!r} version {current.version} was not retained"
            )
        if retained_current != current_snapshot:
            raise SnapshotConflictError(
                f"Current frame {current.frame_id!r} version {current.version} conflicts with "
                "retained state"
            )

        target = versions[target_version]
        if target.operations != current.operations[: target.version]:
            raise SnapshotLineageError(
                f"Frame {current.frame_id!r} version {target_version} is not in the current "
                "operation history"
            )
        return target

    def versions(self, frame_id: str) -> tuple[int, ...]:
        """Return retained versions for inspection in ascending order."""
        return tuple(sorted(self._snapshots.get(frame_id, {})))
