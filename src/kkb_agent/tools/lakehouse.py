"""Typed, deterministic access to catalog series on caller-owned spines."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
from pydantic import Field

from kkb_agent.catalog.align import SeriesSpec, UpsampleRefused, collapse, infer_spine_frequency
from kkb_agent.catalog.retrieval import build_concepts, search
from kkb_agent.catalog.series_source import CatalogSeriesSource
from kkb_agent.frame import MetadataEntry, SourceReference, Spine
from kkb_agent.frame._base import Contract, Identifier

_CATALOG_COLUMNS = (
    "series_id, source, source_ref, name_tr, raw_label, measure_type, sector_scope, "
    "currency_basis, province, unit_raw, unit_normalized, scale_factor, native_freq, "
    "aggregation_rule, coverage_start, coverage_end, observations, nonzero_observations, "
    "retrieved_at, source_hash, cumulative_mode"
)


class LakehouseError(RuntimeError):
    """Base class for safe Lakehouse tool failures."""


class LakehouseUnavailableError(LakehouseError):
    """The configured catalog cannot be queried safely."""


class UnknownSeriesError(LakehouseError):
    """An exact requested series ID does not exist."""


class InvalidSpineError(LakehouseError):
    """The supplied spine cannot represent catalog observations."""


class UnsupportedFrequencyError(LakehouseError):
    """A series would need an unsupported frequency conversion."""


class DuplicateOutputKeyError(LakehouseError):
    """A join would silently overwrite a result column."""


class DuplicateSeriesError(LakehouseError):
    """A join requested the same exact series more than once."""


class CatalogFilters(Contract):
    """Canonical catalog facets; every field maps to an existing catalog column."""

    query: str | None = None
    series_id: Identifier | None = None
    source: Identifier | None = None
    frequency: Identifier | None = None
    measure_type: Identifier | None = None
    sector: str | None = None
    province: str | None = None
    currency: str | None = None


class CatalogMatch(Contract):
    series_id: Identifier
    label: Identifier
    source: Identifier
    frequency: Identifier
    unit: str | None = None
    measure_type: Identifier
    sector: str | None = None
    province: str | None = None
    currency: str | None = None
    coverage_start: date | None = None
    coverage_end: date | None = None
    observations: int = Field(ge=0)
    score: float | None = None
    matched_terms: tuple[str, ...] = ()
    source_reference: str
    source_hash: str | None = None


class DiscoveryResult(Contract):
    filters: CatalogFilters
    matches: tuple[CatalogMatch, ...]
    limit: int = Field(gt=0, le=100)


class LakehouseColumn(Contract):
    key: Identifier
    series_id: Identifier
    label: Identifier
    values: tuple[float | None, ...]
    measure_type: Identifier
    unit: str | None = None
    scale_factor: float = Field(gt=0)
    native_frequency: Identifier
    target_frequency: Identifier
    aggregation_rule: Identifier
    resampled_from: Identifier | None = None
    missing_count: int = Field(ge=0)
    provenance: SourceReference


class LakehouseResult(Contract):
    spine: Spine
    columns: tuple[LakehouseColumn, ...]


class LakehouseTool:
    """Read-only façade over catalog discovery, SeriesSource and frequency alignment."""

    def __init__(self, database: Path | str):
        self._database = str(database)
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._source = CatalogSeriesSource(database)

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        self._source.close()
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._connection is not None:
            return self._connection
        try:
            connection = duckdb.connect(self._database, read_only=True)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
            if not {"series_catalog", "series_observations"} <= tables:
                connection.close()
                raise LakehouseUnavailableError("Lakehouse catalog is not initialized")
            if any(
                connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None
                for table in ("series_catalog", "series_observations")
            ):
                connection.close()
                raise LakehouseUnavailableError("Lakehouse catalog is not populated")
            self._connection = connection
            return connection
        except LakehouseUnavailableError:
            raise
        except Exception as exc:
            raise LakehouseUnavailableError("Lakehouse catalog is unavailable") from exc

    def _rows(self) -> list[dict]:
        try:
            cursor = self._connect().execute(f"SELECT {_CATALOG_COLUMNS} FROM series_catalog")
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        except LakehouseError:
            raise
        except Exception as exc:
            raise LakehouseUnavailableError("Lakehouse catalog could not be read") from exc

    def discover(
        self, filters: CatalogFilters | None = None, *, limit: int = 10
    ) -> DiscoveryResult:
        """Discover catalog rows by exact facets and optional existing lexical ranking."""
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        selected = filters or CatalogFilters()
        rows = [row for row in self._rows() if _matches(row, selected)]
        ranking: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
        if selected.query:
            hits = search(build_concepts(rows), selected.query, limit=100)
            ranking = {hit.concept.key: (hit.score, hit.matched) for hit in hits}
            rows = [
                row
                for row in rows
                if (row["source"], row["raw_label"] or row["name_tr"]) in ranking
            ]
            rows.sort(
                key=lambda row: (
                    -ranking[(row["source"], row["raw_label"] or row["name_tr"])][0],
                    row["series_id"],
                )
            )
        else:
            rows.sort(key=lambda row: row["series_id"])
        return DiscoveryResult(
            filters=selected,
            limit=limit,
            matches=tuple(_catalog_match(row, ranking) for row in rows[:limit]),
        )

    def fetch_series(
        self, series_id: str, spine: Spine, *, output_key: str | None = None
    ) -> LakehouseResult:
        """Fetch one exact series and project it onto the supplied spine."""
        return self.join_series(((output_key or series_id, series_id),), spine)

    def join_series(self, requests: tuple[tuple[str, str], ...], spine: Spine) -> LakehouseResult:
        """Atomically fetch exact IDs as distinct columns on one unchanged spine."""
        _validate_spine(spine)
        keys = [key for key, _ in requests]
        series_ids = [series_id for _, series_id in requests]
        if len(keys) != len(set(keys)):
            raise DuplicateOutputKeyError("Output keys must be unique")
        if len(series_ids) != len(set(series_ids)):
            raise DuplicateSeriesError("Series IDs must be unique within a join")

        loaded = []
        for key, series_id in requests:
            try:
                series = self._source.fetch(series_id)
            except Exception as exc:
                raise LakehouseUnavailableError("Lakehouse observations could not be read") from exc
            if series is None:
                raise UnknownSeriesError(f"Unknown series ID: {series_id}")
            loaded.append((key, series))

        target = infer_spine_frequency(tuple(spine.values))
        columns = tuple(_project(key, series, spine, target) for key, series in loaded)
        return LakehouseResult(spine=spine, columns=columns)


def _matches(row: dict, filters: CatalogFilters) -> bool:
    checks = (
        ("series_id", filters.series_id),
        ("source", filters.source),
        ("native_freq", filters.frequency),
        ("measure_type", filters.measure_type),
        ("sector_scope", filters.sector),
        ("province", filters.province),
        ("currency_basis", filters.currency),
    )
    return all(value is None or row[column] == value for column, value in checks)


def _catalog_match(row: dict, ranking) -> CatalogMatch:
    rank = ranking.get((row["source"], row["raw_label"] or row["name_tr"]))
    return CatalogMatch(
        series_id=row["series_id"],
        label=row["name_tr"],
        source=row["source"],
        frequency=row["native_freq"],
        unit=row["unit_normalized"] or row["unit_raw"] or None,
        measure_type=row["measure_type"],
        sector=row["sector_scope"] or None,
        province=row["province"],
        currency=row["currency_basis"] or None,
        coverage_start=row["coverage_start"],
        coverage_end=row["coverage_end"],
        observations=row["observations"] or 0,
        score=rank[0] if rank else None,
        matched_terms=rank[1] if rank else (),
        source_reference=row["source_ref"],
        source_hash=row["source_hash"] or None,
    )


def _validate_spine(spine: Spine) -> None:
    if spine.kind != "date":
        raise InvalidSpineError("Catalog observations require a date spine")
    if not spine.values:
        raise InvalidSpineError("Spine must not be empty")
    if any(
        current >= following
        for current, following in zip(spine.values, spine.values[1:], strict=False)
    ):
        raise InvalidSpineError("Spine dates must be strictly increasing")


def _project(key, series, spine: Spine, target: str) -> LakehouseColumn:
    spec = SeriesSpec(
        series_id=series.series_id,
        name=series.name,
        values=series.values,
        native_freq=series.native_freq,
        aggregation_rule=series.aggregation_rule,
        unit=series.unit_raw,
        scale_factor=series.scale_factor,
        measure_type=series.measure_type,
    )
    try:
        aligned = collapse(spec, target)
    except UpsampleRefused as exc:
        raise UnsupportedFrequencyError(
            f"Series {series.series_id} cannot be aligned to frequency {target}"
        ) from exc
    values = tuple(aligned.get(period) for period in spine.values)
    provenance = SourceReference(
        source_type=series.source,
        reference=series.series_id,
        retrieved_at=series.retrieved_at,
        raw_sha256=series.source_hash or None,
        metadata=tuple(MetadataEntry(key=name, value=value) for name, value in series.metadata),
    )
    return LakehouseColumn(
        key=key,
        series_id=series.series_id,
        label=series.name or series.series_id,
        values=values,
        measure_type=series.measure_type,
        unit=series.unit_normalized or series.unit_raw or None,
        scale_factor=series.scale_factor,
        native_frequency=series.native_freq,
        target_frequency=target,
        aggregation_rule=series.aggregation_rule,
        resampled_from=series.native_freq if series.native_freq != target else None,
        missing_count=sum(value is None for value in values),
        provenance=provenance,
    )
