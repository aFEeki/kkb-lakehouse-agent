"""Local analytical database connection ownership."""

from contextlib import contextmanager
from pathlib import Path

import duckdb


class DuckDBStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(self.path))
        try:
            yield connection
        finally:
            connection.close()

    def check(self) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1").fetchone() == (1,)
