#!/usr/bin/env python3
"""SCRUM-28 (I6) - check the monthly bulletin against itself.

    .venv/bin/python scripts/check_taraf_partitions.py
    .venv/bin/python scripts/check_taraf_partitions.py --show 20

BDDK publishes the monthly bulletin under ten taraf scopes, and those scopes form three
partitions of the sector. Each has to sum back to its parent:

    Sektör  = Mevduat + Katılım + Kalkınma ve Yatırım          (by bank type)
    Sektör  = Yerli Özel + Kamu + Yabancı                      (by ownership)
    Mevduat = Mevduat-Yerli Özel + Mevduat-Kamu + Mevduat-Yabancı

This is the strongest check in the repo, because unlike the FinTürk unit proof or the
BDDK-EVDS reconciliation it needs no second source and no assumption about what the
numbers should be. The publisher's own arithmetic has to hold, and it holds across every
month, every table and every additive row at once - 89,619 comparisons.

What it catches
---------------
It found the bug it was written to check for. Before SCRUM-98, taraf was not part of the
catalog's frame key and ten bank groups overwrote each other, so nothing summed to
anything.

It then caught something nobody was looking for: four rows filed inside balance-sheet
tables that are actually ratios. A ratio does not add across bank groups, so it stands
out sharply against an identity every genuine balance line satisfies. Tables 12 and 13
label theirs "(YÜZDE)" and "(Yüzde)" while the table caption says "milyon TL"; the unit
was being read from the caption, and _measure_from compared the raw string to "%", which
"YÜZDE" is not. Both are fixed; this script is what would notice a third case.

Only additive series are compared. A rate, ratio or index is meaningless to sum across
bank groups, which is precisely what measure_type is for.

Tolerance
---------
Both relative and absolute, because either alone is wrong here.

BDDK publishes tables 1-4 rounded to whole milyon TL. When the sector total is 2 and the
three groups round to 1, 0 and 0, the relative error is 50% and nothing is wrong. A
purely relative threshold flags 394 of those. A purely absolute threshold would miss a
genuine error on a small line.

Measured over the full crawl: the median absolute difference is 0, the 99.9th percentile
is 1, and the four largest (757, 444, 183, 131 - all in table 3) are under 0.26%
relative. So a breach has to be both proportionally and materially large.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.catalog.build import iter_bddk_aylik  # noqa: E402
from kkb_agent.catalog.schema import MeasureType  # noqa: E402

BRONZE = ROOT / "data" / "bronze" / "bddk" / "aylik"

# Summing a rate or a ratio across bank groups produces a number with no meaning.
ADDITIVE = {MeasureType.STOCK, MeasureType.FLOW, MeasureType.COUNT}

PARTITIONS: list[tuple[str, list[str]]] = [
    ("Sektör", ["Mevduat", "Katılım", "Kalkınma ve Yatırım"]),
    ("Sektör", ["Yerli Özel", "Kamu", "Yabancı"]),
    ("Mevduat", ["Mevduat-Yerli Özel", "Mevduat-Kamu", "Mevduat-Yabancı"]),
]

REL_TOLERANCE_PCT = 0.5
ABS_TOLERANCE = 2.0  # in the table's own published unit


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rel", type=float, default=REL_TOLERANCE_PCT)
    ap.add_argument("--abs", dest="abs_tol", type=float, default=ABS_TOLERANCE)
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()

    if not BRONZE.exists():
        print("BDDK monthly bronze not acquired; nothing to check.")
        return 0

    by_row: dict[tuple[int, str], dict[str, pd.Series]] = defaultdict(dict)
    skipped_non_additive = 0
    for meta, series in iter_bddk_aylik(BRONZE):
        if meta.measure_type not in ADDITIVE:
            skipped_non_additive += 1
            continue
        by_row[(int(meta.source_ref[1:3]), meta.name_tr)][meta.sector_scope] = series

    checked = 0
    breaches: list[tuple[float, float, int, str, str, str]] = []
    worst: list[tuple[float, int, str, str]] = []

    for parent, children in PARTITIONS:
        label = f"{parent} = {' + '.join(children)}"
        n = over = 0
        # The worst relative gap among differences big enough to matter. The unfiltered
        # maximum is always ~100%, because a sector total of 1 against children of 0 is
        # arithmetically a 100% miss and materially nothing; printing that next to "OK"
        # reads like a contradiction.
        largest = 0.0

        for (table_no, row), scopes in by_row.items():
            if parent not in scopes or not all(c in scopes for c in children):
                continue
            p = scopes[parent]
            total = sum((scopes[c] for c in children), start=pd.Series(0.0, index=p.index)).reindex(
                p.index
            )

            mask = total.notna() & (p.abs() > 0)
            if not mask.any():
                continue

            absolute = (total[mask] - p[mask]).abs()
            relative = absolute / p[mask].abs() * 100
            n += int(mask.sum())
            material = relative[absolute > a.abs_tol]
            if len(material):
                largest = max(largest, float(material.max()))

            bad = (relative > a.rel) & (absolute > a.abs_tol)
            over += int(bad.sum())
            for period in relative.index[bad]:
                breaches.append(
                    (
                        float(relative[period]),
                        float(absolute[period]),
                        table_no,
                        row,
                        label,
                        str(period.date()),
                    )
                )
            worst.append((float(absolute.max()), table_no, row, label))

        checked += n
        status = "OK" if over == 0 else f"{over} BREACH"
        print(f"{status:<10} {n:>7,} comparisons   worst material gap {largest:7.3f}%   {label}")

    print(f"\nchecked            : {checked:,} comparisons")
    print(f"non-additive rows  : {skipped_non_additive:,} skipped (rate/ratio/index)")
    print(f"tolerance          : relative > {a.rel}%  AND  absolute > {a.abs_tol}")

    worst.sort(reverse=True)
    print(f"\nlargest absolute differences (top {a.show})")
    for absolute, table_no, row, _ in worst[: a.show]:
        print(f"   t{table_no:02d}  {absolute:>12,.1f}   {row[:58]}")

    if breaches:
        breaches.sort(reverse=True)
        print(f"\nFAIL: {len(breaches)} comparisons past both tolerances")
        for rel, absolute, table_no, row, label, period in breaches[:20]:
            print(
                f"   t{table_no:02d} {period}  rel {rel:7.2f}%  abs {absolute:>12,.1f}  {row[:44]}"
            )
            print(f"        {label}")
        return 1

    print("\nOK: every bank-group partition closes, in every month and every table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
