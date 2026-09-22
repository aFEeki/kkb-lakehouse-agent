"""Conservative selective acceptance; similarity cannot establish financial identity."""

from unittest.mock import Mock

import pytest

from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.vector_index import IndexUnavailable, VectorCandidate, VectorSearch
from kkb_agent.config import Settings


def row(sid="evds.loan", label="Konut kredisi", **changes):
    return (
        dict(
            series_id=sid,
            source="evds",
            raw_label=label,
            measure_type="stock",
            native_freq="M",
            sector_scope="Sektör",
            currency_basis="Toplam",
            province=None,
            unit_normalized="TRY",
            nonzero_observations=60,
        )
        | changes
    )


def index_for(*ids):
    index = Mock()
    index.search.return_value = VectorSearch(
        tuple(VectorCandidate(sid, i / 10, {}) for i, sid in enumerate(ids)), 0.01, 0.002
    )
    return index


@pytest.mark.parametrize("query", ["evds.loan", "Konut kredisi"])
def test_deterministic_lookup_skips_embedding(query):
    index = index_for("evds.loan")
    result = HybridRetrieval(index, mode="selective").resolve(query, [row()])
    assert result.resolution.series_ids == ("evds.loan",)
    index.search.assert_not_called()
    assert not result.trace.vector_invoked
    assert result.trace.mode == "selective"


@pytest.mark.parametrize(
    "query,confidence", [("Ev satın alma borcu", "absent"), ("Konut borcu", "weak")]
)
def test_weak_or_absent_lexical_uses_vector(query, confidence):
    index = index_for("evds.loan")
    result = HybridRetrieval(index, mode="selective").resolve(query, [row()])
    assert result.resolution.series_ids == ("evds.loan",)
    assert result.trace.vector_invoked
    assert result.trace.lexical_confidence == confidence
    index.search.assert_called_once()


@pytest.mark.parametrize(
    "query,bad",
    [
        ("Kullandırılan kredi", {"measure_type": "stock"}),
        ("Katılım kredi", {"sector_scope": "Sektör"}),
        ("Aylık kredi", {"native_freq": "W"}),
    ],
)
def test_top_vector_cannot_bypass_constraints(query, bad):
    good = row(
        "good", measure_type="flow", sector_scope="Katılım" if "Katılım" in query else "Sektör"
    )
    wrong = good | bad | {"series_id": "wrong"}
    result = HybridRetrieval(index_for("wrong", "good"), mode="selective").resolve(
        query, [wrong, good]
    )
    assert result.resolution.series_ids == ("good",)


@pytest.mark.parametrize(
    "rows",
    [
        [
            row("cpi1", "Genel Endeks", measure_type="index", unit_raw="2003=100"),
            row("cpi2", "Genel Endeks", measure_type="index", unit_raw="2025=100"),
        ],
        [row("bddk.loan"), row("evds.loan", source="other", sector_scope="")],
    ],
)
def test_material_variants_are_ambiguous_even_outside_vector_top_k(rows):
    result = HybridRetrieval(index_for(rows[0]["series_id"]), mode="selective").resolve(
        "Genel seri", rows
    )
    assert not result.resolution.resolved
    assert result.ambiguity.candidate_ids == tuple(sorted(r["series_id"] for r in rows))
    assert result.ambiguity.differing_fields
    assert result.trace.ambiguity_detected


def test_convergent_single_compatible_candidate_is_accepted():
    result = HybridRetrieval(index_for("evds.loan"), mode="selective").resolve(
        "Konut borcu", [row()]
    )
    assert result.resolution.series_ids == ("evds.loan",)
    assert not result.ambiguity


@pytest.mark.parametrize("reason", ["index_stale", "embedding_unavailable", "index_missing"])
@pytest.mark.parametrize(
    "question,expected", [("Konut borcu", ("evds.loan",)), ("Ev satın alma borcu", ())]
)
def test_failure_preserves_lexical_result(reason, question, expected):
    index = index_for()
    index.search.side_effect = IndexUnavailable(reason)
    result = HybridRetrieval(index, mode="selective").resolve(question, [row()])
    assert result.resolution.series_ids == expected
    assert result.trace.path == "lexical_fallback"
    assert result.trace.reason == reason


def test_unknown_provider_details_not_exposed():
    index = index_for()
    index.search.side_effect = RuntimeError("secret")
    result = HybridRetrieval(index, mode="selective").resolve("Ev borcu", [row()])
    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    "mode,legacy,expected",
    [
        (None, False, "off"),
        (None, True, "always"),
        ("selective", False, "selective"),
        ("off", True, "off"),
    ],
)
def test_config_compatibility(mode, legacy, expected):
    settings = Settings(_env_file=None, vector_retrieval_mode=mode, vector_retrieval_enabled=legacy)
    assert settings.effective_vector_mode == expected


def test_missing_required_metadata_cannot_win():
    unknown = row("unknown", sector_scope="")
    good = row("good", sector_scope="Katılım")
    result = HybridRetrieval(index_for("unknown", "good"), mode="selective").resolve(
        "Katılım bankaları kredi", [unknown, good]
    )
    assert result.resolution.series_ids == ("good",)


def test_selective_without_index_honest_no_result():
    result = HybridRetrieval(mode="selective").resolve("Ev satın alma borcu", [row()])
    assert not result.resolution.resolved
    assert result.trace.reason == "index_missing"
    assert not result.trace.vector_invoked


def test_explicit_mode_wires_production_index(tmp_path):
    from pathlib import Path

    from kkb_agent.api.main import _default_runner
    from kkb_agent.regression import build_snapshot_database

    snapshot = (
        Path(__file__).resolve().parents[2]
        / "fixtures/regression/published_three_turn_snapshot.json"
    )
    database = tmp_path / "catalog.duckdb"
    build_snapshot_database(snapshot, database)
    runner = _default_runner(
        Settings(
            _env_file=None,
            duckdb_path=database,
            data_dir=tmp_path,
            vector_retrieval_mode="selective",
        )
    )
    assert runner._retrieval.mode == "selective"
    assert runner._retrieval.index is not None
