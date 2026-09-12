from unittest.mock import patch

import pytest
from pydantic import SecretStr

from kkb_agent.llm.client import MIAClient


@pytest.mark.parametrize("key", ["", " ", "API_KEYINIZ"])
def test_missing_credentials_are_lazy(settings, key):
    settings.mia_api_key = SecretStr(key)
    with patch("kkb_agent.llm.client.OpenAI") as factory:
        client = MIAClient(settings)
        factory.assert_not_called()
        with pytest.raises(ValueError, match="MIA_API_KEY"):
            client.get_client()
        factory.assert_not_called()


def test_configured_client_reuses_and_closes(settings):
    settings.mia_api_key = SecretStr("test-only-key")
    with patch("kkb_agent.llm.client.OpenAI") as factory:
        client = MIAClient(settings)
        assert client.chat_model == settings.mia_chat_model
        assert client.embed_model == settings.mia_embed_model
        assert client.ocr_model == settings.mia_ocr_model
        assert client.get_client() is client.get_client()
        factory.assert_called_once_with(
            api_key="test-only-key",
            base_url="https://mia.csp.kloudeks.com/v1",
        )
        client.close()
        factory.return_value.close.assert_called_once()
