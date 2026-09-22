"""Family membership uses label evidence, never generic metadata alone."""

from unittest.mock import Mock

from kkb_agent.catalog.candidate_family import candidate_family
from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.vector_index import VectorCandidate, VectorSearch


def row(sid, label, **extra):
    return (
        dict(
            series_id=sid,
            raw_label=label,
            source="evds",
            measure_type="index",
            native_freq="M",
            unit_normalized="endeks",
            sector_scope="",
            currency_basis="",
            province=None,
            nonzero_observations=60,
        )
        | extra
    )


def test_unrelated_same_measure_frequency_does_not_block_strong_lexical():
    rows = [row("hpi", "Konut Fiyat Endeksi (KFE)"), row("other", "Sanayi Üretim Endeksi")]
    index = Mock()
    result = HybridRetrieval(index, mode="selective").resolve("konut fiyat endeksi", rows)
    assert result.resolution.series_ids == ("hpi",)
    assert result.trace.decision_source == "lexical_unique"
    assert result.trace.family_ids == ("hpi",)
    index.search.assert_not_called()


def test_cpi_version_outside_vector_neighborhood_is_retained():
    rows = [
        row("cpi-new", "Tüketici Fiyat Endeksi (Genel)"),
        row("cpi-old", "Genel Endeks (2003=100)"),
        row("hpi", "Konut Fiyat Endeksi (KFE)"),
    ]
    index = Mock()
    index.search.return_value = VectorSearch((VectorCandidate("cpi-new", 0.1, {}),), 0, 0)
    result = HybridRetrieval(index, mode="selective").resolve("tüketici fiyat endeksi", rows)
    assert result.ambiguity.candidate_ids == ("cpi-new", "cpi-old")
    assert not result.resolution.resolved


def test_housing_source_scope_unknown_remains_competitor():
    rows = [
        row(
            "bddk",
            "Tüketici Kredileri - Konut",
            source="bddk",
            measure_type="stock",
            sector_scope="Sektör",
        ),
        row("evds", "4.1.1. Konut Kredileri", measure_type="stock"),
        row("other", "Mevduat Bakiyesi", measure_type="stock"),
    ]
    assert [r["series_id"] for r in candidate_family(rows, ("bddk",))] == ["bddk", "evds"]
    assert candidate_family(rows, ("bddk",)) == candidate_family(rows[::-1], ("bddk",))


def test_no_transitive_family_drift():
    rows = [
        row("a", "Konut Fiyat Endeksi"),
        row("b", "Fiyat Endeksi"),
        row("c", "Tüketici Fiyat Endeksi"),
    ]
    assert [r["series_id"] for r in candidate_family(rows, ("a",))] == ["a", "b"]


def test_rate_definitions_not_declared_equivalent():
    rows = [
        row("flow-rate", "Konut Kredisi (TL, Akım, %)", measure_type="rate"),
        row("stock-rate", "Konut Kredisi (TL, Stok, %)", measure_type="rate"),
    ]
    assert len(candidate_family(rows, ("flow-rate",))) == 2
