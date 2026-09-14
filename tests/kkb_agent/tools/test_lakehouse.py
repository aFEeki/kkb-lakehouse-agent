from datetime import date
from inspect import getsource
from pathlib import Path

import duckdb
import pytest

from kkb_agent.catalog.schema import CATALOG_DDL, OBSERVATIONS_DDL
from kkb_agent.frame import Spine
from kkb_agent.tools import (
    CatalogFilters,
    DuplicateOutputKeyError,
    DuplicateSeriesError,
    InvalidSpineError,
    LakehouseTool,
    LakehouseUnavailableError,
    UnknownSeriesError,
    UnsupportedFrequencyError,
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "lakehouse.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(CATALOG_DDL)
    connection.execute(OBSERVATIONS_DDL)
    catalog_rows = (
        ("monthly.stock", "bddk_aylik", "Konut Kredileri", "stock", "M", "last", "Sektör"),
        ("monthly.short", "bddk_aylik", "Konut Kredileri Kısa", "stock", "M", "last", "Katılım"),
        ("weekly.rate", "evds", "Konut Kredisi Faizi", "rate", "W", "mean", "Sektör"),
        ("quarterly.index", "evds", "Konut Fiyat Endeksi", "index", "Q", "last", "Sektör"),
    )
    for series_id, source, name, measure, frequency, aggregation, sector in catalog_rows:
        connection.execute(
            "INSERT INTO series_catalog (series_id, source, source_ref, name_tr, raw_label, "
            "measure_type, sector_scope, currency_basis, province, unit_raw, unit_normalized, "
            "scale_factor, cumulative_mode, native_freq, aggregation_rule, observations, "
            "nonzero_observations, source_hash) VALUES (?, ?, ?, ?, ?, ?, ?, 'Toplam', NULL, "
            "?, ?, 1, 'none', ?, ?, 3, 3, ?)",
            [
                series_id,
                source,
                series_id,
                name,
                name,
                measure,
                sector,
                "%" if measure == "rate" else "milyon TL",
                "%" if measure == "rate" else "TRY",
                frequency,
                aggregation,
                "a" * 64,
            ],
        )
    observations = (
        ("monthly.stock", date(2021, 1, 1), 100.0),
        ("monthly.stock", date(2021, 2, 1), None),
        ("monthly.stock", date(2021, 3, 1), 130.0),
        ("monthly.short", date(2021, 1, 1), 10.0),
        ("monthly.short", date(2021, 2, 1), 11.0),
        ("weekly.rate", date(2021, 1, 1), 10.0),
        ("weekly.rate", date(2021, 1, 8), 20.0),
        ("weekly.rate", date(2021, 2, 5), 30.0),
        ("quarterly.index", date(2021, 1, 1), 50.0),
    )
    connection.executemany(
        "INSERT INTO series_observations (series_id, period, value, value_reported) "
        "VALUES (?, ?, ?, ?)",
        [(*row, row[2]) for row in observations],
    )
    connection.close()
    return path


@pytest.fixture
def monthly_spine() -> Spine:
    return Spine(values=(date(2021, 1, 1), date(2021, 2, 1), date(2021, 3, 1)))


def test_exact_discovery_returns_typed_catalog_metadata(database):
    with LakehouseTool(database) as tool:
        result = tool.discover(CatalogFilters(series_id="monthly.stock"))

    assert [match.series_id for match in result.matches] == ["monthly.stock"]
    assert result.matches[0].source == "bddk_aylik"
    assert result.matches[0].measure_type == "stock"
    assert result.matches[0].source_reference == "monthly.stock"


def test_combined_discovery_filters_narrow_without_substitution(database):
    filters = CatalogFilters(source="bddk_aylik", measure_type="stock", sector="Katılım")
    with LakehouseTool(database) as tool:
        result = tool.discover(filters)

    assert [match.series_id for match in result.matches] == ["monthly.short"]


def test_text_discovery_reuses_deterministic_catalog_ranking(database):
    with LakehouseTool(database) as tool:
        first = tool.discover(CatalogFilters(query="konut kredisi"))
        second = tool.discover(CatalogFilters(query="konut kredisi"))

    assert first == second
    assert first.matches
    assert all(match.score is not None for match in first.matches)


def test_discovery_no_match_and_limit_are_explicit(database):
    with LakehouseTool(database) as tool:
        empty = tool.discover(CatalogFilters(source="missing"))
        limited = tool.discover(CatalogFilters(), limit=2)

    assert empty.matches == ()
    assert len(limited.matches) == limited.limit == 2
    assert [item.series_id for item in limited.matches] == sorted(
        item.series_id for item in limited.matches
    )


def test_fetch_projects_exact_series_onto_unchanged_spine(database, monthly_spine):
    original = monthly_spine.model_dump_json()
    with LakehouseTool(database) as tool:
        result = tool.fetch_series("monthly.stock", monthly_spine, output_key="loans")

    assert result.spine == monthly_spine
    assert result.spine.model_dump_json() == original
    assert len(result.columns[0].values) == len(monthly_spine.values) == 3
    assert result.columns[0].values == (100.0, None, 130.0)
    assert result.columns[0].missing_count == 1


def test_weekly_series_uses_existing_monthly_mean_alignment(database, monthly_spine):
    with LakehouseTool(database) as tool:
        column = tool.fetch_series("weekly.rate", monthly_spine).columns[0]

    assert column.values == (15.0, 30.0, None)
    assert column.aggregation_rule == "mean"
    assert column.resampled_from == "W"
    assert column.target_frequency == "M"


def test_fetch_preserves_exact_id_and_provenance(database, monthly_spine):
    with LakehouseTool(database) as tool:
        column = tool.fetch_series("monthly.stock", monthly_spine).columns[0]

    assert column.series_id == "monthly.stock"
    assert column.provenance.reference == "monthly.stock"
    assert column.provenance.source_type == "bddk_aylik"
    assert column.provenance.raw_sha256 == "a" * 64


def test_unknown_series_fails_without_substitution(database, monthly_spine):
    with LakehouseTool(database) as tool:
        with pytest.raises(UnknownSeriesError, match="unknown.series"):
            tool.fetch_series("unknown.series", monthly_spine)


def test_join_keeps_all_rows_and_ragged_edge_as_none(database, monthly_spine):
    with LakehouseTool(database) as tool:
        result = tool.join_series(
            (("loans", "monthly.stock"), ("short", "monthly.short")), monthly_spine
        )

    assert result.spine == monthly_spine
    assert len(result.spine.values) == 3
    assert [column.key for column in result.columns] == ["loans", "short"]
    assert result.columns[1].values == (10.0, 11.0, None)


def test_join_rejects_duplicate_keys_and_series(database, monthly_spine):
    with LakehouseTool(database) as tool:
        with pytest.raises(DuplicateOutputKeyError):
            tool.join_series((("same", "monthly.stock"), ("same", "weekly.rate")), monthly_spine)
        with pytest.raises(DuplicateSeriesError):
            tool.join_series((("one", "monthly.stock"), ("two", "monthly.stock")), monthly_spine)


def test_invalid_member_makes_join_fail_atomically(database, monthly_spine):
    with LakehouseTool(database) as tool:
        with pytest.raises(UnknownSeriesError):
            tool.join_series(
                (("valid", "monthly.stock"), ("invalid", "does.not.exist")), monthly_spine
            )


def test_empty_unordered_duplicate_and_datetime_spines_are_rejected(database):
    invalid = (
        Spine(values=()),
        Spine(values=(date(2021, 2, 1), date(2021, 1, 1))),
        Spine.model_construct(values=(date(2021, 1, 1), date(2021, 1, 1))),
        Spine.model_construct(kind="datetime", values=()),
    )
    with LakehouseTool(database) as tool:
        for spine in invalid:
            with pytest.raises(InvalidSpineError):
                tool.fetch_series("monthly.stock", spine)


def test_unsupported_upsampling_is_a_typed_error(database, monthly_spine):
    with LakehouseTool(database) as tool:
        with pytest.raises(UnsupportedFrequencyError):
            tool.fetch_series("quarterly.index", monthly_spine)


def test_uninitialized_or_missing_catalog_fails_safely(tmp_path, monthly_spine):
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    for path in (empty, tmp_path / "missing.duckdb"):
        with pytest.raises(LakehouseUnavailableError) as caught:
            with LakehouseTool(path) as tool:
                tool.fetch_series("monthly.stock", monthly_spine)
        assert str(path) not in str(caught.value)


def test_empty_catalog_tables_are_not_ready(tmp_path):
    empty = tmp_path / "empty-tables.duckdb"
    connection = duckdb.connect(str(empty))
    connection.execute(CATALOG_DDL)
    connection.execute(OBSERVATIONS_DDL)
    connection.close()

    with pytest.raises(LakehouseUnavailableError, match="not populated"):
        with LakehouseTool(empty):
            pass


def test_public_tool_has_no_arbitrary_sql_or_model_api(database):
    public = {name for name in dir(LakehouseTool) if not name.startswith("_")}

    assert public == {"close", "discover", "fetch_series", "join_series"}
    source = getsource(LakehouseTool)
    assert "kkb_agent.llm" not in source
    assert "MIA" not in source
