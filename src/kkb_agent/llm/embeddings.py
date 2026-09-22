"""Bounded MIA embeddings behind a provider-neutral metadata retrieval boundary."""

from collections.abc import Sequence
from math import isfinite
from numbers import Real
from typing import Protocol

from kkb_agent.llm.client import MIAClient


class EmbeddingError(ValueError):
    """Safe error: provider exceptions and payloads must not escape this boundary."""


class EmbeddingProvider(Protocol):
    model_id: str

    def embed_texts(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


def validate_vectors(vectors, count: int, dimension: int | None = None) -> list[list[float]]:
    try:
        if len(vectors) != count:
            raise ValueError
        result = []
        for vector in vectors:
            if not vector or any(isinstance(v, bool) or not isinstance(v, Real) for v in vector):
                raise ValueError
            values = [float(v) for v in vector]
            dimension = dimension or len(values)
            if len(values) != dimension or not all(isfinite(v) for v in values):
                raise ValueError
            # Cosine distance has no meaning for a zero vector.
            if not any(values):
                raise ValueError
            result.append(values)
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise EmbeddingError("invalid_embedding_response") from exc


class MIAEmbeddingProvider:
    def __init__(self, client: MIAClient):
        self.client = client
        self.model_id = client.embed_model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = (
                self.client.get_client()
                .with_options(timeout=15.0, max_retries=0)
                .embeddings.create(model=self.model_id, input=list(texts), encoding_format="float")
            )
            items = sorted(response.data, key=lambda item: item.index)
            if [item.index for item in items] != list(range(len(texts))):
                raise EmbeddingError("invalid_embedding_response")
            return validate_vectors([item.embedding for item in items], len(texts))
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError("embedding_unavailable") from None

    def embed_query(self, text: str) -> list[float]:
        if "qwen3-embedding" in self.model_id.casefold():
            text = (
                "Instruct: Given a Turkish financial question, retrieve the catalog series "
                "whose measure and scope answer the question.\nQuery: " + text
            )
        return self.embed_texts([text])[0]
