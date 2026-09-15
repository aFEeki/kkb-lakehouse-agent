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
import hashlib
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.catalog.build import (  # noqa: E402
    bronze_provenance,
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


# Checks the built catalog has to pass. Each compares the data against its own published
# arithmetic rather than against what we expected, and each exits non-zero on breach.
#
# They run after the build, not before: a catalog that fails one is still written, because
# seeing the broken numbers is how you diagnose it. What the gate changes is the exit
# code, so nothing downstream treats a failed build as a good one.
INVARIANT_CHECKS = [
    ("de-cumulation closes (I1)", "check_decumulation.py"),
    ("sign and classification (I2, I3)", "check_cumulative_consistency.py"),
    ("bank-group partitions (I6)", "check_taraf_partitions.py"),
    ("FinTürk units (I4)", "check_finturk_units.py"),
]


def check_arguments(script: str, *, bronze: Path, catalog: Path) -> list[str]:
    """Point one check at the data just built, rather than at whatever is on disk.

    Without this each check reads its own default path, so a fixture build would report
    on the full lake - green for data the build never touched (SCRUM-32).
    """
    if script in ("check_decumulation.py", "check_cumulative_consistency.py"):
        return ["--db", str(catalog)]
    if script == "check_taraf_partitions.py":
        return ["--bronze", str(bronze / "aylik")]
    if script == "check_finturk_units.py":
        return ["--bronze", str(bronze / "finturk")]
    return []


def run_checks(root: Path, *, bronze: Path = BRONZE, catalog: Path = GOLD) -> int:
    """Run every invariant check. Returns the number that failed."""
    import subprocess

    failed = 0
    print(f"\n{'=' * 72}\ninvariants")
    for title, script in INVARIANT_CHECKS:
        path = root / "scripts" / script
        if not path.exists():
            print(f"   SKIP  {title:<32} ({script} missing)")
            continue
        proc = subprocess.run(
            [sys.executable, str(path), *check_arguments(script, bronze=bronze, catalog=catalog)],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        ok = proc.returncode == 0
        failed += not ok
        print(f"   {'ok  ' if ok else 'FAIL'}  {title}")
        if not ok:
            for line in (proc.stdout or proc.stderr).strip().splitlines()[-12:]:
                print(f"         {line}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=GOLD)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument(
        "--no-check",
        action="store_true",
        help="write the catalog without running the invariant checks",
    )
    # So CI can build from the committed fixture instead of the full lake, which it does
    # not have. The build path is identical either way - what changes is only how much
    # bronze it is pointed at (SCRUM-32).
    ap.add_argument("--bronze", type=Path, default=BRONZE, help="BDDK bronze directory")
    ap.add_argument("--silver", type=Path, default=SILVER, help="EVDS silver directory")
    a = ap.parse_args()
    bronze, silver = a.bronze, a.silver

    t0 = time.time()
    pairs = []

    if (bronze / "aylik").exists():
        got = list(iter_bddk_aylik(bronze / "aylik"))
        print(f"BDDK aylık   : {len(got):>6} series")
        pairs += got
    else:
        print("BDDK aylık   : not acquired")

    if (bronze / "haftalik").exists():
        got = list(iter_bddk_haftalik(bronze / "haftalik"))
        print(f"BDDK haftalık: {len(got):>6} series")
        pairs += got
    else:
        print("BDDK haftalık: not acquired")

    if (bronze / "finturk").exists():
        got = list(iter_bddk_finturk(bronze / "finturk"))
        print(f"BDDK fintürk : {len(got):>6} series")
        pairs += got
    else:
        print("BDDK fintürk : not acquired")

    if silver.exists():
        got = list(iter_evds(silver, EVDS_CONFIG))
        print(f"EVDS         : {len(got):>6} series")
        pairs += got
    else:
        print("EVDS         : not ingested")

    if not pairs:
        print("\nNothing to build. Acquire data first.")
        return 1

    # Freshness and reproducibility (SCRUM-29, I8). Stamped per source because a monthly
    # series is assembled from 66 files: what identifies it is which acquisition produced
    # it, not which single file.
    print()
    for source, manifest in [
        ("bddk_aylik", bronze / "manifest_aylik.jsonl"),
        ("bddk_haftalik", bronze / "manifest_haftalik.jsonl"),
        ("bddk_finturk", bronze / "manifest_finturk.jsonl"),
    ]:
        prov = bronze_provenance(manifest, source)
        if prov is None:
            continue
        stamped = 0
        for meta, _ in pairs:
            if str(meta.source) == source:
                meta.source_hash = prov.digest
                if meta.retrieved_at is None:
                    meta.retrieved_at = prov.retrieved_at
                stamped += 1
        when = f"{prov.retrieved_at:%Y-%m-%d %H:%M}" if prov.retrieved_at else "unknown"
        print(
            f"provenance   : {prov.short}  {prov.files:>6,} files  "
            f"fetched {when}  -> {stamped:,} series"
        )

    # EVDS has no per-file manifest: it is fetched series by series from an API, and the
    # committed config is what pins the scope. Hashing that config is the equivalent
    # statement - "these series, this selection" - and it changes whenever the selection
    # does. retrieved_at is already on each series, from the parquet it was written to.
    if EVDS_CONFIG.exists():
        config_digest = hashlib.sha256(EVDS_CONFIG.read_bytes()).hexdigest()
        stamped = 0
        for meta, _ in pairs:
            if str(meta.source) == "evds":
                meta.source_hash = config_digest
                stamped += 1
        print(f"provenance   : evds:{config_digest[:12]}  {EVDS_CONFIG.name} -> {stamped:,} series")

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
    # A fixture build writes to a temporary directory outside the repo, where
    # relative_to would raise rather than print a path.
    where = a.out.relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out
    print(f"built in     : {time.time() - t0:.1f}s -> {where}")
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

    if a.no_check:
        print("\n--no-check: invariants not run. The catalog is unverified.")
        return 0

    failed = run_checks(ROOT, bronze=bronze, catalog=a.out)
    if failed:
        print(f"\n{failed} invariant(s) FAILED. The catalog is written but must not be served.")
        return 1
    print("\nall invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
