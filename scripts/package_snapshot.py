#!/usr/bin/env python3
"""SCRUM-33 - package the data snapshot for release.

    .venv/bin/python scripts/package_snapshot.py
    .venv/bin/python scripts/package_snapshot.py --out dist/

The previous snapshot was tarred by hand, and both things that went wrong with it were
consequences of that: it was cut one commit before `nonzero_observations` existed, so every
consumer of the release failed on a column the code required, and it carried a stray
`.claude/` directory that git ignores but `tar` does not, because tar walks the filesystem
and not the index.

So this refuses to package a catalog that cannot answer turn 1 - the same check the API
gates on, rather than a second weaker copy of it - and it excludes by an explicit
allowlist-shaped filter instead of trusting whatever happens to be on disk.

Prints the fingerprint to publish in the release notes: anyone who rebuilds can compare it
and know whether they arrived at the same numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.api.main import _turn1_catalog_ready  # noqa: E402

DATA = ROOT / "data"
GOLD = DATA / "gold" / "lakehouse.duckdb"

# Never shipped, whatever the filesystem holds. `.claude` is the rule the repo states and
# the one a hand-rolled tar silently broke; the rest is noise that inflates the download.
EXCLUDED_NAMES = {".claude", "__pycache__", ".DS_Store", ".pytest_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".wal", ".tgz"}


def _excluded(path: Path) -> str | None:
    """Why this path is not shipped, or None if it is."""
    for part in path.parts:
        if part in EXCLUDED_NAMES:
            return part
    if path.suffix in EXCLUDED_SUFFIXES:
        return path.suffix
    return None


def fingerprint(catalog: Path) -> tuple[str, int, int]:
    """A digest of what the catalog holds, stable across rebuilds of the same bronze."""
    import duckdb

    connection = duckdb.connect(str(catalog), read_only=True)
    try:
        rows = connection.execute(
            "SELECT series_id, observations, nonzero_observations "
            "FROM series_catalog ORDER BY series_id"
        ).fetchall()
        observations = connection.execute("SELECT count(*) FROM series_observations").fetchone()[0]
    finally:
        connection.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()[:16], len(rows), observations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--allow-unready",
        action="store_true",
        help="package even if the catalog cannot answer turn 1 (do not use for a release)",
    )
    args = parser.parse_args()

    if not GOLD.exists():
        print(f"{GOLD} not found. Run scripts/build_catalog.py first.")
        return 1

    if not _turn1_catalog_ready(GOLD) and not args.allow_unready:
        print(
            "Refusing to package: this catalog cannot answer turn 1.\n"
            "That is exactly the state the last release shipped in. Rebuild with\n"
            "scripts/build_catalog.py, or pass --allow-unready if you know why."
        )
        return 1

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout.strip()

    stamp = datetime.now(UTC).date().isoformat()
    args.out.mkdir(parents=True, exist_ok=True)
    archive = args.out / f"kkb-data-snapshot-{stamp}-{commit}.tgz"

    digest, series, observations = fingerprint(GOLD)

    skipped: dict[str, int] = {}
    shipped = 0
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(DATA.rglob("*")):
            if path.is_dir():
                continue
            reason = _excluded(path.relative_to(ROOT))
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            tar.add(path, arcname=str(path.relative_to(ROOT)))
            shipped += 1

    size_mb = archive.stat().st_size / 1_000_000
    print(f"archive      : {archive.relative_to(ROOT)}  ({size_mb:,.1f} MB, {shipped:,} files)")
    print(f"built from   : {commit}{'  [WORKING TREE DIRTY]' if dirty else ''}")
    print(f"series       : {series:,}")
    print(f"observations : {observations:,}")
    print(f"fingerprint  : {digest}")
    if skipped:
        print("excluded     : " + ", ".join(f"{k} x{v}" for k, v in sorted(skipped.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
