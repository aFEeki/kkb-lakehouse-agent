"""Environment configuration; constructing settings never contacts a service."""

from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mia_api_key: SecretStr = SecretStr("")
    mia_base_url: str = "https://mia.csp.kloudeks.com/v1"
    mia_chat_model: str = "kkbhackathon2026/Qwen3.8-27B"
    mia_embed_model: str = "kkbhackathon2026/Qwen3-Embedding-8B"
    mia_ocr_model: str = "kkbhackathon2026/Unlimited-OCR"
    evds_api_key: SecretStr = SecretStr("")
    data_dir: Path = Path("./data")
    duckdb_path: Path | None = None
    lancedb_path: Path | None = None
    searxng_url: str = "http://localhost:8888"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @model_validator(mode="after")
    def resolve_storage_paths(self) -> "Settings":
        self.data_dir = self.data_dir.expanduser().resolve()
        self.duckdb_path = (
            (self.duckdb_path or self.data_dir / "gold/lakehouse.duckdb").expanduser().resolve()
        )
        self.lancedb_path = (
            (self.lancedb_path or self.data_dir / "gold/.lancedb").expanduser().resolve()
        )
        return self
