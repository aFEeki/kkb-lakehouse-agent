"""Curated EVDS ingestion into per-series silver Parquet files."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from kkb_agent.ingest.evds import EVDSObservation

_SERIES_CODE = re.compile(r"^[A-Za-z0-9._-]+$")
_FREQUENCIES = {"daily", "weekly", "monthly", "quarterly", "yearly"}
_DAILY_WINDOW_DAYS = 999
_SILVER_SCHEMA = pa.schema(
    [
        ("series_code", pa.string()),
        ("period", pa.date32()),
        ("value", pa.decimal128(38, 10)),
        ("retrieved_at", pa.timestamp("us", tz="UTC")),
    ]
)


class EVDSFetcher(Protocol):
    def fetch_series(
        self, series_code: str, *, start_date: date, end_date: date
    ) -> tuple[EVDSObservation, ...]: ...


@dataclass(frozen=True)
class CuratedSeries:
    code: str
    name: str
    frequency: str


@dataclass(frozen=True)
class CuratedEVDSConfig:
    start_date: date
    end_date: date
    is_full_evds_catalog: bool
    scope_note: str
    known_gaps: tuple[str, ...]
    series: tuple[CuratedSeries, ...]


@dataclass(frozen=True)
class SeriesCoverage:
    series_code: str
    name: str
    status: str
    observations: int
    missing_values: int
    coverage_start: str | None
    coverage_end: str | None
    retrieved_at: str
    output_path: str


def load_curated_config(path: Path) -> CuratedEVDSConfig:
    """Load and validate the committed curated-series configuration."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        start_date = date.fromisoformat(payload["start_date"])
        end_date = date.fromisoformat(payload["end_date"])
        raw_series = payload["series"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid curated EVDS configuration") from exc

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    if not isinstance(raw_series, list) or not raw_series:
        raise ValueError("series must contain at least one entry")

    entries: list[CuratedSeries] = []
    seen: set[str] = set()
    for raw in raw_series:
        try:
            entry = CuratedSeries(
                code=str(raw["code"]).strip(),
                name=str(raw["name"]).strip(),
                frequency=str(raw["frequency"]).strip().lower(),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Each series entry requires code, name and frequency") from exc
        if not _SERIES_CODE.fullmatch(entry.code):
            raise ValueError(f"Invalid EVDS series code: {entry.code!r}")
        if not entry.name:
            raise ValueError(f"Series {entry.code!r} requires a name")
        if entry.frequency not in _FREQUENCIES:
            raise ValueError(f"Unsupported frequency for {entry.code!r}: {entry.frequency!r}")
        if entry.code in seen:
            raise ValueError(f"Duplicate EVDS series code: {entry.code}")
        seen.add(entry.code)
        entries.append(entry)

    is_full_evds_catalog = payload.get("is_full_evds_catalog", False)
    scope_note = str(payload.get("scope_note", "")).strip()
    known_gaps = tuple(str(item).strip() for item in payload.get("known_gaps", []))
    if not isinstance(is_full_evds_catalog, bool):
        raise ValueError("is_full_evds_catalog must be a boolean")
    if not scope_note:
        raise ValueError("scope_note must describe the curated scope")
    if not is_full_evds_catalog and not any(known_gaps):
        raise ValueError("A curated subset must state its known gaps")

    return CuratedEVDSConfig(
        start_date=start_date,
        end_date=end_date,
        is_full_evds_catalog=is_full_evds_catalog,
        scope_note=scope_note,
        known_gaps=known_gaps,
        series=tuple(entries),
    )


class CuratedEVDSIngestor:
    def __init__(
        self,
        client: EVDSFetcher,
        output_dir: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._output_dir = output_dir
        self._clock = clock or (lambda: datetime.now(UTC))

    def ingest(self, config: CuratedEVDSConfig) -> dict[str, object]:
        """Fetch every configured series and write data plus a coverage report."""

        self._output_dir.mkdir(parents=True, exist_ok=True)
        coverage: list[SeriesCoverage] = []
        for entry in config.series:
            retrieved_at = self._clock().astimezone(UTC)
            observations = self._fetch_all(entry, config.start_date, config.end_date)
            output_path = self._output_dir / f"{entry.code}.parquet"
            self._write_series(output_path, entry.code, observations, retrieved_at)
            periods = [item.period for item in observations]
            coverage.append(
                SeriesCoverage(
                    series_code=entry.code,
                    name=entry.name,
                    status="ok" if observations else "no_data",
                    observations=len(observations),
                    missing_values=sum(item.value is None for item in observations),
                    coverage_start=min(periods).isoformat() if periods else None,
                    coverage_end=max(periods).isoformat() if periods else None,
                    retrieved_at=retrieved_at.isoformat(),
                    output_path=output_path.name,
                )
            )

        report: dict[str, object] = {
            "generated_at": self._clock().astimezone(UTC).isoformat(),
            "requested_start": config.start_date.isoformat(),
            "requested_end": config.end_date.isoformat(),
            "is_full_evds_catalog": config.is_full_evds_catalog,
            "scope_note": config.scope_note,
            "known_gaps": list(config.known_gaps),
            "curated_series_count": len(config.series),
            "series_with_data": sum(item.status == "ok" for item in coverage),
            "series_without_data": sum(item.status == "no_data" for item in coverage),
            "series": [asdict(item) for item in coverage],
        }
        (self._output_dir / "coverage.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return report

    def _fetch_all(
        self, entry: CuratedSeries, start_date: date, end_date: date
    ) -> tuple[EVDSObservation, ...]:
        by_period: dict[date, EVDSObservation] = {}
        for window_start, window_end in _request_windows(entry.frequency, start_date, end_date):
            for observation in self._client.fetch_series(
                entry.code, start_date=window_start, end_date=window_end
            ):
                if start_date <= observation.period <= end_date:
                    by_period[observation.period] = observation
        return tuple(by_period[period] for period in sorted(by_period))

    @staticmethod
    def _write_series(
        path: Path,
        series_code: str,
        observations: tuple[EVDSObservation, ...],
        retrieved_at: datetime,
    ) -> None:
        table = pa.Table.from_arrays(
            [
                pa.array([series_code] * len(observations), type=pa.string()),
                pa.array([item.period for item in observations], type=pa.date32()),
                pa.array([item.value for item in observations], type=pa.decimal128(38, 10)),
                pa.array([retrieved_at] * len(observations), type=pa.timestamp("us", tz="UTC")),
            ],
            schema=_SILVER_SCHEMA,
        )
        pq.write_table(table, path)


def _request_windows(
    frequency: str, start_date: date, end_date: date
) -> Iterable[tuple[date, date]]:
    if frequency != "daily":
        yield start_date, end_date
        return

    window_start = start_date
    while window_start <= end_date:
        window_end = min(end_date, window_start + timedelta(days=_DAILY_WINDOW_DAYS - 1))
        yield window_start, window_end
        window_start = window_end + timedelta(days=1)
