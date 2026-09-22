"""Lazy, server-side MIA client configuration."""

import httpx
from openai import OpenAI

from kkb_agent.config import Settings

# Observed healthy routing: 7–13s; planning: ~15s per request.
# Phase timeouts bound actual I/O; this is not an aggregate wall-clock deadline.
MODEL_TIMEOUT = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)


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
            self._client = OpenAI(
                api_key=key,
                base_url=self._settings.mia_base_url,
                timeout=MODEL_TIMEOUT,
                max_retries=0,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
