import duckdb
import pytest

from kkb_agent.catalog.duckdb_store import DuckDBStore


def test_bootstrap_and_close(settings):
    store = DuckDBStore(settings.duckdb_path)
    assert store.check()
    assert settings.duckdb_path.is_file()
    with store.connect() as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(duckdb.ConnectionException):
        connection.execute("SELECT 1")


def test_closes_after_failure(settings):
    with pytest.raises(RuntimeError), DuckDBStore(settings.duckdb_path).connect() as connection:
        raise RuntimeError("test")
    with pytest.raises(duckdb.ConnectionException):
        connection.execute("SELECT 1")
