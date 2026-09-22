"""Metadata-only, replaceable LanceDB catalog index. DuckDB remains authoritative."""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import duckdb
import pyarrow as pa

from kkb_agent.catalog.lance_store import LanceStore
from kkb_agent.llm.embeddings import EmbeddingProvider, validate_vectors

INDEX_VERSION = "catalog-metadata-v1"
TABLE = "semantic_catalog"
FIELDS = (
    "series_id",
    "name_tr",
    "raw_label",
    "source",
    "measure_type",
    "native_freq",
    "unit_normalized",
    "unit_raw",
    "scale_factor",
    "sector_scope",
    "province",
    "currency_basis",
)


class IndexUnavailable(ValueError):
    """Controlled readiness reason; never contains connection/provider internals."""


def metadata(row: dict) -> dict:
    return {key: row[key] for key in FIELDS if row.get(key) is not None and row[key] != ""}


def embedding_text(row: dict) -> str:
    return " | ".join(f"{key}: {value}" for key, value in metadata(row).items())


def catalog_rows(path: Path) -> list[dict]:
    with duckdb.connect(str(path), read_only=True) as connection:
        columns = (*FIELDS, "nonzero_observations")
        cursor = connection.execute(
            f"SELECT {', '.join(columns)} FROM series_catalog ORDER BY series_id"
        )
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def fingerprint(rows: list[dict]) -> str:
    ids = [r.get("series_id") for r in rows]
    if any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid_or_duplicate_series_id")
    payload = [metadata(row) for row in sorted(rows, key=lambda row: row["series_id"])]
    encoded = json.dumps(
        [INDEX_VERSION, payload], sort_keys=True, ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class VectorCandidate:
    series_id: str
    distance: float
    metadata: dict


@dataclass(frozen=True)
class VectorSearch:
    candidates: tuple[VectorCandidate, ...]
    embedding_seconds: float
    search_seconds: float


class SemanticIndex:
    def __init__(self, path: Path, provider: EmbeddingProvider):
        self.store = LanceStore(path)
        self.provider = provider

    def build(self, rows: list[dict], *, batch_size: int = 64) -> int:
        digest = fingerprint(rows)
        if not rows:
            raise ValueError("empty_catalog")
        if not 1 <= batch_size <= 64:
            raise ValueError("batch_size must be between 1 and 64")
        rows = sorted(rows, key=lambda row: row["series_id"])
        vectors = []
        dimension = None
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset : offset + batch_size]
            embedded = validate_vectors(
                self.provider.embed_texts([embedding_text(row) for row in batch]),
                len(batch),
                dimension,
            )
            dimension = len(embedded[0])
            vectors.extend(embedded)
        schema = pa.schema(
            [
                ("series_id", pa.string()),
                ("vector", pa.list_(pa.float64(), dimension)),
                ("embedding_text", pa.string()),
                ("metadata_json", pa.string()),
                ("fingerprint", pa.string()),
                ("model_id", pa.string()),
                ("index_version", pa.string()),
            ]
        )
        records = [
            dict(
                series_id=row["series_id"],
                vector=vector,
                embedding_text=embedding_text(row),
                metadata_json=json.dumps(metadata(row), sort_keys=True, ensure_ascii=False),
                fingerprint=digest,
                model_id=self.provider.model_id,
                index_version=INDEX_VERSION,
            )
            for row, vector in zip(rows, vectors, strict=True)
        ]
        # All embedding batches validate before one atomic table overwrite/commit.
        table = self.store.connect().create_table(
            TABLE, pa.Table.from_pylist(records, schema=schema), mode="overwrite"
        )
        if table.count_rows() != len(rows):
            raise IndexUnavailable("index_count_mismatch")
        self._validated_table(rows)
        return len(rows)

    def _validated_table(self, rows: list[dict]):
        if not self.store.path.exists():
            raise IndexUnavailable("index_missing")
        try:
            connection = self.store.connect()
            if TABLE not in connection.list_tables().tables:
                raise IndexUnavailable("index_empty")
            table = connection.open_table(TABLE)
        except IndexUnavailable:
            raise
        except Exception:
            raise IndexUnavailable("index_unavailable") from None
        vector_type = table.schema.field("vector").type
        if not pa.types.is_fixed_size_list(vector_type) or vector_type.list_size < 1:
            raise IndexUnavailable("index_unavailable")
        if table.count_rows() == 0:
            raise IndexUnavailable("index_empty")
        records = (
            table.search()
            .select(["series_id", "fingerprint", "model_id", "index_version"])
            .limit(table.count_rows())
            .to_list()
        )
        if not records:
            raise IndexUnavailable("index_empty")
        digest = fingerprint(rows)
        if (
            len(records) != len(rows)
            or {r["series_id"] for r in records} != {r["series_id"] for r in rows}
            or any(
                r["fingerprint"] != digest
                or r["index_version"] != INDEX_VERSION
                or r["model_id"] != self.provider.model_id
                for r in records
            )
        ):
            raise IndexUnavailable("index_stale")
        return table

    def readiness(self, rows: list[dict]) -> str:
        try:
            self._validated_table(rows)
            return "ready"
        except IndexUnavailable as exc:
            return "empty" if str(exc) in {"index_missing", "index_empty"} else "unavailable"
        except Exception:
            return "unavailable"

    def search(self, question: str, rows: list[dict], *, top_k: int = 20) -> VectorSearch:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        table = self._validated_table(rows)
        dimension = table.schema.field("vector").type.list_size
        start = perf_counter()
        vector = validate_vectors([self.provider.embed_query(question)], 1, dimension)[0]
        embedded = perf_counter()
        # Exact flat search: no approximate index or stochastic ANN tie selection.
        # Retrieve all small catalog distances so top-k boundary ties use series_id.
        results = (
            table.search(vector)
            .distance_type("cosine")
            .select(["series_id", "metadata_json", "_distance"])
            .limit(len(rows))
            .to_list()
        )
        candidates = []
        for result in results:
            distance = float(result["_distance"])
            if not math.isfinite(distance):
                raise IndexUnavailable("invalid_index_distance")
            candidates.append(
                VectorCandidate(result["series_id"], distance, json.loads(result["metadata_json"]))
            )
        candidates.sort(key=lambda candidate: (candidate.distance, candidate.series_id))
        return VectorSearch(tuple(candidates[:top_k]), embedded - start, perf_counter() - embedded)
