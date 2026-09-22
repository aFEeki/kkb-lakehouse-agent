from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kkb_agent.llm.embeddings import EmbeddingError, MIAEmbeddingProvider


def test_mia_existing_client_batch_order_and_bounded_timeout():
    client = Mock(embed_model="configured-model")
    api = client.get_client.return_value.with_options.return_value
    api.embeddings.create.return_value = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[0, 1]),
            SimpleNamespace(index=0, embedding=[1, 0]),
        ]
    )
    provider = MIAEmbeddingProvider(client)
    assert provider.embed_texts(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    client.get_client.return_value.with_options.assert_called_once_with(timeout=15.0, max_retries=0)
    api.embeddings.create.assert_called_once_with(
        model="configured-model", input=["first", "second"], encoding_format="float"
    )


@pytest.mark.parametrize("indices", [[0, 0], [1], [0, 2]])
def test_invalid_provider_indices_rejected(indices):
    client = Mock(embed_model="test")
    client.get_client.return_value.with_options.return_value.embeddings.create.return_value = (
        SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1, 0]) for i in indices])
    )
    with pytest.raises(EmbeddingError, match="invalid_embedding_response"):
        MIAEmbeddingProvider(client).embed_texts(["a", "b"])


def test_provider_exception_redacted():
    client = Mock(embed_model="test")
    client.get_client.side_effect = RuntimeError("API_KEY=secret")
    with pytest.raises(EmbeddingError, match="^embedding_unavailable$"):
        MIAEmbeddingProvider(client).embed_query("question")


def test_qwen_query_instruction_only():
    client = Mock(embed_model="configured/Qwen3-Embedding-8B")
    api = client.get_client.return_value.with_options.return_value
    api.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(index=0, embedding=[1, 0])]
    )
    provider = MIAEmbeddingProvider(client)
    provider.embed_query("konut")
    text = api.embeddings.create.call_args.kwargs["input"][0]
    assert text.startswith("Instruct: ") and text.endswith("\nQuery: konut")
    provider.embed_texts(["catalog metadata"])
    assert api.embeddings.create.call_args.kwargs["input"] == ["catalog metadata"]
