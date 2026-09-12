"""Lazy, server-side MIA client configuration."""

from openai import OpenAI

from kkb_agent.config import Settings


class MIAClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self.chat_model = settings.mia_chat_model
        self.embed_model = settings.mia_embed_model
        self.ocr_model = settings.mia_ocr_model
        self._client: OpenAI | None = None

    def get_client(self) -> OpenAI:
        key = self._settings.mia_api_key.get_secret_value().strip()
        if not key or key == "API_KEYINIZ":
            raise ValueError("MIA_API_KEY must be configured before using MIA.")
        if self._client is None:
            self._client = OpenAI(api_key=key, base_url=self._settings.mia_base_url)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
