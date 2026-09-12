import importlib

import pytest

from kkb_agent.config import Settings


@pytest.mark.parametrize(
    "module",
    [
        "",
        ".llm",
        ".frame",
        ".ingest",
        ".catalog",
        ".transform",
        ".tools",
        ".agent",
        ".api",
    ],
)
def test_package_imports(module):
    assert importlib.import_module(f"kkb_agent{module}")


def test_secretless_defaults(settings, tmp_path):
    assert settings.mia_api_key.get_secret_value() == ""
    assert settings.duckdb_path == tmp_path / "gold/lakehouse.duckdb"
    assert settings.lancedb_path == tmp_path / "gold/.lancedb"
    assert not settings.duckdb_path.exists()


def test_env_file_and_environment_override(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("MIA_API_KEY=test-secret\nDATA_DIR=./from-file\nUNKNOWN=value\n")
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "override.duckdb"))
    result = Settings(_env_file=env)
    assert result.data_dir == tmp_path / "from-file"
    assert result.duckdb_path == tmp_path / "override.duckdb"
    assert result.mia_api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(result)
