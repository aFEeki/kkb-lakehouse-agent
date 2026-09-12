"""Minimal adapter contract; fetching archives raw data before parsing."""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class SourceAdapter(Protocol):
    def discover(self) -> Iterable[str]:
        """Return opaque source references, interpreted only by this adapter."""
        ...

    def fetch(self, reference: str, destination: Path) -> Path:
        """Archive raw bytes under destination and return their local path."""
        ...

    def parse(self, raw_path: Path) -> Iterable[Mapping[str, object]]:
        """Yield source records for later normalization; not analytics-ready data."""
        ...
