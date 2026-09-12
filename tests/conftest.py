import pytest

from kkb_agent.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    # Never read the developer's .env or storage paths, even during module imports.
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
        monkeypatch.delenv(field, raising=False)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, mia_api_key="", evds_api_key="")
