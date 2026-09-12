"""Local catalog index bootstrap; no observations or embeddings are written."""

from pathlib import Path

import lancedb


class LanceStore:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        self.path.mkdir(parents=True, exist_ok=True)
        return lancedb.connect(str(self.path))

    def check(self) -> bool:
        self.connect().list_tables()
        return True
