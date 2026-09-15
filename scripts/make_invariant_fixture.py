#!/usr/bin/env python3
"""SCRUM-32 - slice the bronze lake into the fixture CI runs the invariants against.

    .venv/bin/python scripts/make_invariant_fixture.py

CI has no data, which is why the invariant suite never ran there and a change that broke
de-cumulation could go green. The whole lake is 590 MB and cannot be committed; this cuts
the smallest slice that still exercises all three checks, and commits it.

What each check needs, and why the slice is shaped this way:

    taraf partitions (I6)   every one of the ten bank-group scopes, or a partition has a
                            missing child and is skipped rather than checked
    de-cumulation (I1)      an income-statement table, because those are the series that
                            accumulate year-to-date, and at least one complete calendar
                            year, because a year whose December is unpublished is skipped
    FinTürk units (I4)      tablo1 and tablo6 of the same quarter - the check rebuilds
                            table 1's published figures out of table 6's per-capita
                            columns, and a factor-of-1000 unit error is what it catches

Regenerate after re-crawling bronze. The fixture is real published data, not synthetic:
an invariant that holds on numbers we invented proves nothing about the numbers we serve.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze" / "bddk"
FIXTURE = ROOT / "tests" / "fixtures" / "invariants" / "bronze"

# 13 months: classify() needs at least 13 observations to reach a verdict, and the
# de-cumulation check needs 2021 complete - a year without its December is skipped.
MONTHS = [f"2021-{m:02d}" for m in range(1, 13)] + ["2022-01"]

# Table 2 is the income statement, so its rows are the year-to-date ones de-cumulation has
# to undo. Table 4 is a balance sheet, which must come through untouched - a fixture with
# only accumulating series would not catch a transform that accumulates everything.
TABLES = ("t02", "t04")

QUARTERS = ("2021-3", "2021-6")
FINTURK_TABLES = ("tablo1.json", "tablo6.json")


def main() -> int:
    if not BRONZE.exists():
        print(f"{BRONZE} not found. Acquire bronze first.")
        return 1

    if FIXTURE.exists():
        shutil.rmtree(FIXTURE)

    copied = total = 0

    for month in MONTHS:
        source = BRONZE / "aylik" / month
        if not source.is_dir():
            print(f"  missing {source}")
            continue
        target = FIXTURE / "aylik" / month
        target.mkdir(parents=True, exist_ok=True)
        for path in sorted(source.glob("t*_taraf*.json")):
            if not path.stem.startswith(TABLES):
                continue
            shutil.copy2(path, target / path.name)
            copied += 1
            total += path.stat().st_size

    for quarter in QUARTERS:
        source = BRONZE / "finturk" / quarter
        if not source.is_dir():
            print(f"  missing {source}")
            continue
        target = FIXTURE / "finturk" / quarter
        target.mkdir(parents=True, exist_ok=True)
        for name in FINTURK_TABLES:
            if (source / name).exists():
                shutil.copy2(source / name, target / name)
                copied += 1
                total += (source / name).stat().st_size

    print(f"fixture: {copied:,} files, {total / 1_000_000:.1f} MB -> {FIXTURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
