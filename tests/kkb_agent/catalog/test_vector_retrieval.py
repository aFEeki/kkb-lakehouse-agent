"""Offline semantic index, hybrid filtering, and degradation contracts."""

from fractions import Fraction

import pytest

from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.retrieval import build_concepts
from kkb_agent.catalog.series_resolver import resolve_series
from kkb_agent.catalog.vector_index import SemanticIndex, embedding_text, fingerprint
from kkb_agent.llm.embeddings import EmbeddingError, validate_vectors


def row(sid="loans", label="Konut kredileri", **kwargs):
    return (
        dict(
            series_id=sid,
            raw_label=label,
            name_tr=label,
            source="bddk_aylik",
            measure_type="stock",
            native_freq="M",
            sector_scope="Sektör",
            currency_basis="Toplam",
            province=None,
            unit_normalized="TRY",
            nonzero_observations=60,
            **{},
        )
        | kwargs
    )


class FakeEmbeddings:
    model_id = "deterministic-test-v1"

    def __init__(self):
        self.queries = 0
        self.batches = []
        self.failure = None

    def embed_texts(self, texts):
        self.batches.append(len(texts))
        return [[1.0, 0.0] if "konut" in text.lower() else [0.0, 1.0] for text in texts]

    def embed_query(self, text):
        self.queries += 1
        if self.failure:
            raise self.failure
        return [1.0, 0.0]


@pytest.fixture
def indexed(tmp_path):
    rows = [row(), row("deposits", "Mevduat")]
    provider = FakeEmbeddings()
    index = SemanticIndex(tmp_path / "lance", provider)
    index.build(rows, batch_size=1)
    return rows, provider, index


def test_metadata_is_deterministic_and_excludes_arbitrary_fields():
    original = row()
    enriched = original | {"value": 1234, "secret": "do-not-embed", "retrieved_at": "tomorrow"}
    assert embedding_text(original) == embedding_text(enriched)
    assert embedding_text(dict(reversed(list(original.items())))) == embedding_text(original)
    assert "None" not in embedding_text(original)
    assert fingerprint([original]) == fingerprint([enriched])
    assert embedding_text({"series_id": "x"}) == "series_id: x"


def test_build_replace_batch_count_and_readiness(indexed):
    rows, provider, index = indexed
    assert provider.batches == [1, 1]
    assert index.build(list(reversed(rows))) == 2
    assert index._validated_table(rows).count_rows() == 2
    assert index.readiness(rows) == "ready"
    with pytest.raises(ValueError, match="duplicate"):
        index.build([rows[0], rows[0]])
    with pytest.raises(ValueError, match="empty_catalog"):
        index.build([])
    assert index.readiness(rows) == "ready"


@pytest.mark.parametrize(
    "vectors",
    [
        [[1, 2], [1]],
        [[float("nan"), 1]],
        [[float("inf"), 0]],
        [[0, 0]],
        [[True, 1]],
        [[10**1000, 1]],
        [["1", 2]],
    ],
)
def test_bad_vectors_rejected(vectors):
    with pytest.raises(EmbeddingError):
        validate_vectors(vectors, len(vectors), 2)


def test_count_and_fraction():
    with pytest.raises(EmbeddingError):
        validate_vectors([[1, 2]], 2)
    assert validate_vectors([[Fraction(1, 2), 1]], 1) == [[0.5, 1.0]]


def test_search_bounded_order_metadata(indexed):
    rows, provider, index = indexed
    result = index.search("ev almak için borç", rows, top_k=1)
    assert [c.series_id for c in result.candidates] == ["loans"]
    assert result.candidates[0].metadata["native_freq"] == "M"
    assert provider.queries == 1
    assert result.candidates == index.search("ev almak için borç", rows, top_k=1).candidates
    for k in (0, 101, True):
        with pytest.raises(ValueError):
            index.search("query", rows, top_k=k)


def test_ties_and_no_duplicate_candidates(tmp_path):
    rows = [row("z"), row("a")]
    index = SemanticIndex(tmp_path, FakeEmbeddings())
    index.build(rows)
    assert [c.series_id for c in index.search("q", rows, top_k=1).candidates] == ["a"]


def test_paraphrase_adds_candidate(indexed):
    rows, _, index = indexed
    question = "Ev satın almak için kalan borç"
    lexical = HybridRetrieval().resolve(question, rows)
    assert not lexical.resolution.resolved
    hybrid = HybridRetrieval(index).resolve(question, rows)
    assert hybrid.resolution.series_ids == ("loans",)
    assert hybrid.trace.path == "hybrid"
    assert hybrid.trace.vector_candidates == 2
    assert hybrid.trace.selected_series_ids == ("loans",)


@pytest.mark.parametrize(
    "change,question",
    [
        ({"measure_type": "flow"}, "konut kredisi bakiyesi"),
        ({"native_freq": "W"}, "aylık konut kredisi bakiyesi"),
        ({"sector_scope": "Katılım"}, "sektör konut kredisi bakiyesi"),
        ({"province": "İstanbul"}, "konut kredisi bakiyesi"),
        ({"currency_basis": "YP"}, "TP konut kredisi bakiyesi"),
        ({"source": "evds"}, "BDDK konut kredisi bakiyesi"),
        ({"measure_type": None}, "konut kredisi bakiyesi"),
    ],
)
def test_metadata_mismatch_never_wins(tmp_path, change, question):
    good = row("good", currency_basis="TP" if "TP" in question else "Toplam")
    bad = good | {"series_id": "a-wrong"} | change
    rows = [bad, good]
    index = SemanticIndex(tmp_path, FakeEmbeddings())
    index.build(rows)
    assert HybridRetrieval(index).resolve(question, rows).resolution.series_ids == ("good",)


def test_unsatisfied_facets_do_not_fall_back_unfiltered(tmp_path):
    rows = [row(sector_scope="Katılım")]
    index = SemanticIndex(tmp_path, FakeEmbeddings())
    index.build(rows)
    assert (
        not HybridRetrieval(index).resolve("kamu konut kredisi bakiyesi", rows).resolution.resolved
    )


def test_exact_match_and_disabled_skip_provider(indexed):
    rows, provider, index = indexed
    assert HybridRetrieval(index).resolve("loans", rows).resolution.series_ids == ("loans",)
    assert provider.queries == 0
    query = "konut kredisi bakiyesi"
    expected = resolve_series(build_concepts(rows), query, candidates=rows, limit=8)
    assert HybridRetrieval().resolve(query, rows).resolution == expected
    assert HybridRetrieval(index).resolve(query, rows).resolution.series_ids == expected.series_ids


@pytest.mark.parametrize(
    "failure", [TimeoutError("SECRET"), RuntimeError("SECRET"), EmbeddingError("SECRET")]
)
def test_provider_failure_is_sanitized_lexical_fallback(indexed, failure):
    rows, provider, index = indexed
    provider.failure = failure
    query = "konut kredisi bakiyesi"
    result = HybridRetrieval(index).resolve(query, rows)
    assert result.resolution == HybridRetrieval().resolve(query, rows).resolution
    assert result.trace.path == "lexical_fallback"
    assert "SECRET" not in repr(result)


def test_missing_stale_and_model_mismatch_skip_embedding(indexed, tmp_path):
    rows, provider, index = indexed
    missing = HybridRetrieval(SemanticIndex(tmp_path / "missing", provider)).resolve("konut", rows)
    assert missing.trace.reason == "index_missing"
    stale_rows = [rows[0] | {"unit_normalized": "USD"}, rows[1]]
    assert HybridRetrieval(index).resolve("konut", stale_rows).trace.reason == "index_stale"
    provider.model_id = "changed"
    assert HybridRetrieval(index).resolve("konut", rows).trace.reason == "index_stale"
    assert provider.queries == 0


def test_failed_rebuild_preserves_previous_table(indexed):
    rows, provider, index = indexed
    provider.embed_texts = lambda texts: [[float("nan"), 1] for _ in texts]
    with pytest.raises(EmbeddingError):
        index.build(rows)
    assert index.readiness(rows) == "ready"


def test_malformed_query_vector_fallback(indexed):
    rows, provider, index = indexed
    provider.embed_query = lambda text: [1, 2, 3]
    assert (
        HybridRetrieval(index).resolve("konut", rows).trace.reason == "invalid_embedding_response"
    )


def test_failed_later_batch_keeps_previous_index(indexed):
    rows, provider, index = indexed
    calls = 0

    def fail_second(texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("provider-secret")
        return [[1, 0] for _ in texts]

    provider.embed_texts = fail_second
    with pytest.raises(TimeoutError):
        index.build(rows, batch_size=1)
    assert index.readiness(rows) == "ready"


def test_unknown_or_conflicting_exact_id_never_embeds(indexed):
    rows, provider, index = indexed
    unknown = HybridRetrieval(index).resolve("evds.TP.NONEXIST", rows)
    assert not unknown.resolution.resolved
    assert unknown.trace.reason == "exact_series_id"
    conflict = HybridRetrieval(index).resolve("loans akım", rows)
    assert not conflict.resolution.resolved
    assert provider.queries == 0


def test_exact_identifier_with_punctuation(tmp_path):
    rows = [row("evds.TP.TEST", source="evds")]
    provider = FakeEmbeddings()
    result = HybridRetrieval(SemanticIndex(tmp_path, provider)).resolve("(evds.TP.TEST).", rows)
    assert result.resolution.series_ids == ("evds.TP.TEST",)
    assert provider.queries == 0


def test_lancedb_connection_failure_is_optional(indexed):
    rows, _, index = indexed

    def unavailable():
        raise RuntimeError("secret")

    index.store.connect = unavailable
    assert index.readiness(rows) == "unavailable"
    result = HybridRetrieval(index).resolve("konut kredisi", rows)
    assert result.trace.path == "lexical_fallback"
    assert result.trace.reason == "index_unavailable"


def test_stale_catalog_addition(indexed):
    rows, provider, index = indexed
    changed = rows + [row("new")]
    assert HybridRetrieval(index).resolve("konut", changed).trace.reason == "index_stale"
    assert provider.queries == 0
