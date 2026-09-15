#!/usr/bin/env python3
"""SCRUM-24 / SCRUM-27 (I1) - check that de-cumulation reproduces the published year.

    .venv/bin/python scripts/check_decumulation.py
    .venv/bin/python scripts/check_decumulation.py --tolerance 0.001

599 series in the catalog accumulate within the year. The catalog stores each period's
own figure in `value` and the published running total in `value_reported`, so a join
cannot read six months of profit as one month's.

That transformation has to be checked, because a wrong de-cumulation is invisible: the
numbers stay plausible, they are simply the wrong size. The check needs no second source:

    sum of de-cumulated months in a year  ==  that year's published December figure

If January is 47,347 and the published June total is 422,459, then the six de-cumulated
months must add back to 422,459 - and the December total must equal the whole year. An
off-by-one in the year boundary, a fabricated fill for a missing month, or a series
misclassified as accumulating all break this.

A year whose December is not published is skipped rather than compared against its last
available month, which would be a different claim. The current year is therefore normally
skipped, and that is correct: the closure is only defined once the year is complete.

Exits non-zero on breach, so it can gate a rebuild.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold" / "lakehouse.duckdb"

TOLERANCE = 0.005  # 0.5%, the same order as BDDK's own rounding


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=GOLD)
    ap.add_argument("--tolerance", type=float, default=TOLERANCE)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument(
        "--min-series-years",
        type=int,
        default=1,
        help="fail if fewer series-years were closed than this (guards a vacuous pass)",
    )
    a = ap.parse_args()

    if not a.db.exists():
        print(f"{a.db} not found. Run scripts/build_catalog.py first.")
        return 1

    con = duckdb.connect(str(a.db), read_only=True)

    total_ytd = con.execute(
        "SELECT count(*) FROM series_catalog WHERE cumulative_mode = 'ytd'"
    ).fetchone()[0]
    if not total_ytd:
        print("No accumulating series in the catalog; nothing to check.")
        return 0

    # December's published figure is the year's total; the de-cumulated months must add
    # back to it. Both columns come from the same row, so no join can misalign them.
    rows = con.execute(
        """
        SELECT c.series_id,
               c.name_tr,
               c.sector_scope,
               year(o.period)                                          AS yr,
               sum(o.value)                                            AS parts,
               max(CASE WHEN month(o.period) = 12 THEN o.value_reported END) AS reported,
               count(o.value)                                          AS months,
               count(*)                                                AS rows_in_year
        FROM   series_catalog c
        JOIN   series_observations o USING (series_id)
        WHERE  c.cumulative_mode = 'ytd'
        GROUP  BY 1, 2, 3, 4
        HAVING reported IS NOT NULL AND abs(reported) > 1
        """
    ).fetchall()

    if not rows:
        # Not "nothing wrong" - nothing checked. A transform that stopped classifying
        # anything as accumulating would leave no year to close and, reported as a pass,
        # would hide the very break this exists to catch.
        print(
            f"FAIL: {total_ytd} accumulating series, but no complete year to close "
            "against. Nothing was verified."
        )
        return 1

    breaches: list[tuple] = []
    incomplete = 0
    worst = 0.0
    by_series: set[str] = set()

    for series_id, name, scope, yr, parts, reported, months, rows_in_year in rows:
        by_series.add(series_id)
        if months != rows_in_year:
            # A null inside the year means a month we could not de-cumulate, so the sum
            # is of fewer months than the year holds and cannot be compared.
            incomplete += 1
            continue
        drift = abs(float(parts) - float(reported)) / abs(float(reported))
        worst = max(worst, drift)
        if drift > a.tolerance:
            breaches.append((drift, series_id, name, scope, yr, float(parts), float(reported)))

    checked = len(rows) - incomplete
    print(f"accumulating series : {total_ytd:,}")
    print(f"series-years closed : {checked:,}  ({len(by_series):,} distinct series)")
    print(f"skipped, gap in year: {incomplete:,}")
    print(f"tolerance           : {a.tolerance:.3%}")
    print(f"worst drift         : {worst:.4%}")

    if checked < a.min_series_years:
        print(
            f"\nFAIL: only {checked:,} series-years closed, expected at least "
            f"{a.min_series_years:,}. Nothing was verified."
        )
        return 1

    if breaches:
        breaches.sort(reverse=True)
        print(f"\nFAIL: {len(breaches)} series-years do not close")
        grouped: dict[str, int] = defaultdict(int)
        for _, _, name, _, _, _, _ in breaches:
            grouped[name] += 1
        for drift, _sid, name, scope, yr, parts, reported in breaches[: a.show]:
            print(
                f"   {yr}  {name[:38]:<38} {scope[:14]:<14} "
                f"parts {parts:>14,.0f}  reported {reported:>14,.0f}  {drift:.2%}"
            )
        print("\n   most affected labels:")
        for name, n in sorted(grouped.items(), key=lambda kv: -kv[1])[:5]:
            print(f"      {n:>4}  {name[:56]}")
        return 1

    print("\nOK: every de-cumulated year adds back to the figure its source published.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
