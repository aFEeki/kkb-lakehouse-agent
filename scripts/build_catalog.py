#!/usr/bin/env python3
"""SCRUM-20 / SCRUM-21 - build the series catalog from bronze.

    .venv/bin/python scripts/build_catalog.py
    .venv/bin/python scripts/build_catalog.py --show 20

Writes data/gold/lakehouse.duckdb with two tables:

    series_catalog       one row per series - what it is, its unit, whether it
                         accumulates, what frequency, which province
    series_observations  long format, one row per series per period

Deterministic: the same bronze always produces the same catalog. That is what lets the
snapshot be shared and everyone arrive at identical numbers.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.catalog.build import (  # noqa: E402
    iter_bddk_aylik,
    iter_bddk_finturk,
    iter_bddk_haftalik,
    iter_evds,
    to_frames,
)
from kkb_agent.catalog.schema import (  # noqa: E402
    CATALOG_DDL,
    CATALOG_TABLE,
    INDEXES_DDL,
    OBSERVATIONS_DDL,
    OBSERVATIONS_TABLE,
)

BRONZE = ROOT / "data" / "bronze" / "bddk"
SILVER = ROOT / "data" / "silver" / "evds"
GOLD = ROOT / "data" / "gold" / "lakehouse.duckdb"
EVDS_CONFIG = ROOT / "config" / "evds-series.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=GOLD)
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()

    t0 = time.time()
    pairs = []

    if (BRONZE / "aylik").exists():
        got = list(iter_bddk_aylik(BRONZE / "aylik"))
        print(f"BDDK aylık   : {len(got):>6} series")
        pairs += got
    else:
        print("BDDK aylık   : not acquired")

    if (BRONZE / "haftalik").exists():
        got = list(iter_bddk_haftalik(BRONZE / "haftalik"))
        print(f"BDDK haftalık: {len(got):>6} series")
        pairs += got
    else:
        print("BDDK haftalık: not acquired")

    if (BRONZE / "finturk").exists():
        got = list(iter_bddk_finturk(BRONZE / "finturk"))
        print(f"BDDK fintürk : {len(got):>6} series")
        pairs += got
    else:
        print("BDDK fintürk : not acquired")

    if SILVER.exists():
        got = list(iter_evds(SILVER, EVDS_CONFIG))
        print(f"EVDS         : {len(got):>6} series")
        pairs += got
    else:
        print("EVDS         : not ingested")

    if not pairs:
        print("\nNothing to build. Acquire data first.")
        return 1

    catalog, observations = to_frames(pairs)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.out.exists():
        a.out.unlink()  # rebuild from scratch; the catalog is derived, never patched

    con = duckdb.connect(str(a.out))
    con.execute(CATALOG_DDL)
    con.execute(OBSERVATIONS_DDL)
    con.register("cat_df", catalog)
    con.register("obs_df", observations)
    con.execute(f"INSERT INTO {CATALOG_TABLE} SELECT * FROM cat_df")
    con.execute(f"INSERT INTO {OBSERVATIONS_TABLE} SELECT * FROM obs_df")
    for stmt in INDEXES_DDL:
        con.execute(stmt)

    print(f"\n{'=' * 72}")
    print(f"catalog      : {len(catalog):,} series")
    print(f"observations : {len(observations):,} rows")
    print(f"built in     : {time.time() - t0:.1f}s -> {a.out.relative_to(ROOT)}")
    print("=" * 72)

    for title, sql in [
        ("by source", f"SELECT source, count(*) n FROM {CATALOG_TABLE} GROUP BY 1 ORDER BY 2 DESC"),
        (
            "by measure type",
            f"SELECT measure_type, count(*) n FROM {CATALOG_TABLE} GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "by cumulative mode",
            f"SELECT cumulative_mode, count(*) n FROM {CATALOG_TABLE} GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "by frequency",
            f"SELECT native_freq, count(*) n FROM {CATALOG_TABLE} GROUP BY 1 ORDER BY 2 DESC",
        ),
        (
            "unusable - not servable until resolved",
            f"""SELECT cumulative_mode, measure_type, count(*) n FROM {CATALOG_TABLE}
             WHERE cumulative_mode='ambiguous' OR measure_type='unknown'
                OR unit_normalized='' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5""",
        ),
    ]:
        print(f"\n{title}")
        for row in con.execute(sql).fetchall():
            print("   ", "  ".join(str(x) for x in row))

    print("\nsample - what turn 1 needs (housing loans and its rate)")
    rows = con.execute(f"""
        SELECT series_id, name_tr, unit_raw, measure_type, cumulative_mode,
               native_freq, observations
        FROM {CATALOG_TABLE}
        WHERE lower(name_tr) LIKE '%konut%'
        ORDER BY source, series_id LIMIT {a.show}
    """).fetchall()
    for r in rows:
        print(f"   {r[0][:46]:<46} {str(r[2]):<10} {r[3]:<6} {r[4]:<5} {r[5]} n={r[6]}")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
