"""Instance-scoped successful analysis-frame retention for multi-turn requests."""

from kkb_agent.frame import AnalysisFrame


class AnalysisFrameStoreError(RuntimeError):
    pass


class AnalysisFrameNotFoundError(AnalysisFrameStoreError):
    pass


class AnalysisFrameConflictError(AnalysisFrameStoreError):
    pass


class StaleAnalysisFrameError(AnalysisFrameStoreError):
    pass


class AnalysisFrameStore:
    def __init__(self) -> None:
        self._frames: dict[str, dict[int, AnalysisFrame]] = {}

    def put(self, frame: AnalysisFrame) -> None:
        stored = AnalysisFrame.model_validate(frame.model_dump())
        versions = self._frames.setdefault(frame.frame_id, {})
        existing = versions.get(frame.version)
        if existing is not None and existing != stored:
            raise AnalysisFrameConflictError(
                f"Analysis {frame.frame_id!r} version {frame.version} conflicts with stored state"
            )
        versions[frame.version] = stored

    def get(self, analysis_id: str, version: int) -> AnalysisFrame:
        frame = self._frames.get(analysis_id, {}).get(version)
        if frame is None:
            raise AnalysisFrameNotFoundError(
                f"Analysis {analysis_id!r} version {version} was not found"
            )
        return frame

    def get_current(self, analysis_id: str, version: int) -> AnalysisFrame:
        frame = self.get(analysis_id, version)
        latest = max(self._frames[analysis_id])
        if version != latest:
            raise StaleAnalysisFrameError(
                f"Analysis {analysis_id!r} version {version} is stale; current version is {latest}"
            )
        return frame
