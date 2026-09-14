"""Load one catalog series in the shape the frame's add_column handler needs.

The handler takes a `SeriesSource` protocol rather than a database, so the frame layer
never imports DuckDB and can be tested without one. This is the production implementation.

`value` is read, not `value_reported`: for the 599 accumulating series those differ, and
`value` is the one that means "this period", which is what a column holds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from kkb_agent.agent.handlers import LoadedSeries

_COLUMNS = (
    "series_id, source, name_tr, measure_type, unit_raw, unit_normalized, scale_factor, "
    "native_freq, aggregation_rule, sector_scope, province, currency_basis, "
    "source_hash, retrieved_at, cumulative_mode"
)


def _as_utc(stamp: datetime | None) -> datetime | None:
    """SourceReference requires an aware timestamp.

    Everything written to the catalog is UTC - BDDK manifests record "+00:00" and the
    EVDS parquet column is tz-aware - so a naive value here means a storage type dropped
    the zone rather than that the instant is unknown.
    """
    if stamp is None or stamp.tzinfo is not None:
        return stamp
    return stamp.replace(tzinfo=UTC)


class CatalogSeriesSource:
    """Reads series out of the gold catalog. Read-only; never writes."""

    def __init__(self, database: Path | str):
        self._path = str(database)
        self._connection: duckdb.DuckDBPyConnection | None = None

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._connection is None:
            self._connection = duckdb.connect(self._path, read_only=True)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def fetch(self, series_reference: str) -> LoadedSeries | None:
        con = self._connect()
        meta = con.execute(
            f"SELECT {_COLUMNS} FROM series_catalog WHERE series_id = ?", [series_reference]
        ).fetchone()
        if meta is None:
            return None

        (
            series_id,
            source,
            name,
            measure_type,
            unit_raw,
            unit_normalized,
            scale_factor,
            native_freq,
            aggregation_rule,
            sector_scope,
            province,
            currency_basis,
            source_hash,
            retrieved_at,
            cumulative_mode,
        ) = meta

        rows = con.execute(
            "SELECT period, value FROM series_observations WHERE series_id = ? ORDER BY period",
            [series_reference],
        ).fetchall()

        # The facets travel with the series so the column can say which cut it is. A
        # frame holding "konut kredisi" for Katılım and one holding it for the whole
        # sector are otherwise indistinguishable once the numbers are on a chart.
        metadata = tuple(
            (key, str(value))
            for key, value in (
                ("sector_scope", sector_scope),
                ("province", province or "Türkiye geneli"),
                ("currency_basis", currency_basis),
                ("cumulative_mode", cumulative_mode),
            )
            if value
        )

        return LoadedSeries(
            series_id=series_id,
            name=name,
            values={period: value for period, value in rows},
            native_freq=str(native_freq),
            aggregation_rule=str(aggregation_rule),
            measure_type=str(measure_type),
            unit_raw=unit_raw or "",
            unit_normalized=unit_normalized or "",
            scale_factor=float(scale_factor or 1.0),
            source=str(source),
            source_hash=source_hash or "",
            retrieved_at=_as_utc(retrieved_at),
            metadata=metadata,
        )
