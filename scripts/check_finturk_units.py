#!/usr/bin/env python3
"""SCRUM-25 - prove FinTürk table 6's units from the data instead of asserting them.

    .venv/bin/python scripts/check_finturk_units.py

Table 6 is the one FinTürk table whose unit is not a property of the table. Its dropdown
label says "(TL)", but it holds a branch count, a population-per-branch figure and four
per-capita amounts. Nothing in the payload states a unit, so the choice between TL and
Bin TL for the per-capita columns decides a 1000x error - and it cannot be settled by
looking at the numbers and deciding they seem about right.

It can be settled arithmetically, because table 6 carries enough to rebuild table 1:

    Yurtiçi Şube Sayısı x Şubeye Düşen Nüfus         = the province's population
    that population x Kişi Başı Nakdi Kredi          = the province's cash loans
    which table 1 publishes directly, in Bin TL

If the per-capita column were Bin TL the reconstruction would miss by a factor of 1000.
Run across every province and quarter we hold, it agrees to within a rounding error,
which is what licenses the unit recorded in FINTURK_T06_COLUMN.

Exits non-zero if any province drifts past the tolerance, so this can gate a rebuild.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze" / "bddk" / "finturk"

# Column positions within a FinTürk row's `cell` array, after province and bank group.
T06_BRANCHES, T06_POP_PER_BRANCH, T06_LOAN_PER_CAPITA = 0, 1, 2
T01_CASH_LOANS = 1  # "Nakdi Krediler" - not column 0, which adds takipteki alacaklar

TOLERANCE_PCT = 0.5


def sector_rows(payload: dict) -> dict[str, list]:
    """Province -> value cells, for the sector aggregate only."""
    rows = payload["data"]["rows"] if isinstance(payload.get("data"), dict) else []
    return {
        str(r["cell"][3]): r["cell"][5:]
        for r in rows
        if len(r.get("cell") or []) > 5 and str(r["cell"][4]).upper() == "SEKTÖR"
    }


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["Json"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance", type=float, default=TOLERANCE_PCT)
    ap.add_argument("--show", type=int, default=5, help="worst N deviations to print")
    # So CI can point the same check at the committed fixture instead of the full lake,
    # which it does not have. The check itself is identical either way (SCRUM-32).
    ap.add_argument("--bronze", type=Path, default=BRONZE, help="FinTürk bronze directory")
    ap.add_argument(
        "--min-provinces",
        type=int,
        default=1,
        help="fail if fewer province-quarters were compared (guards a vacuous pass)",
    )
    a = ap.parse_args()

    if not a.bronze.exists():
        print("FinTürk bronze not acquired; nothing to check.")
        return 0

    checked, breaches, worst = 0, [], []

    for period_dir in sorted(p for p in a.bronze.iterdir() if p.is_dir()):
        p6, p1 = load(period_dir / "tablo6.json"), load(period_dir / "tablo1.json")
        if p6 is None or p1 is None:
            continue
        t6, t1 = sector_rows(p6), sector_rows(p1)

        for province, cells in t6.items():
            if province not in t1 or len(cells) <= T06_LOAN_PER_CAPITA:
                continue
            branches = cells[T06_BRANCHES]
            pop_per_branch = cells[T06_POP_PER_BRANCH]
            per_capita = cells[T06_LOAN_PER_CAPITA]
            actual = t1[province][T01_CASH_LOANS] if len(t1[province]) > T01_CASH_LOANS else None
            if not branches or not pop_per_branch or not actual:
                continue  # a province with no branches has no per-capita figure to check

            # per-capita is TL; table 1 publishes Bin TL, hence the /1000.
            implied = per_capita * branches * pop_per_branch / 1000
            drift = abs(implied - actual) / actual * 100
            checked += 1
            worst.append((drift, period_dir.name, province, implied, actual))
            if drift > a.tolerance:
                breaches.append((drift, period_dir.name, province))

    if checked < a.min_provinces:
        print(
            f"FAIL: {checked:,} province-quarters compared, expected at least "
            f"{a.min_provinces:,}. Is table 1 or table 6 missing? Nothing was verified."
        )
        return 1

    worst.sort(reverse=True)
    print(f"checked   : {checked:,} province-quarters")
    print(f"tolerance : {a.tolerance}%")
    print(f"worst     : {worst[0][0]:.3f}%")
    print(f"\nlargest deviations (top {a.show})")
    print(f"   {'period':<10}{'il':<14}{'implied (bin TL)':>20}{'published':>18}{'drift':>9}")
    for drift, period, province, implied, actual in worst[: a.show]:
        print(f"   {period:<10}{province:<14}{implied:>20,.0f}{actual:>18,.0f}{drift:>8.3f}%")

    if breaches:
        print(f"\nFAIL: {len(breaches)} province-quarters past tolerance")
        for drift, period, province in breaches[:10]:
            print(f"   {period}  {province}  {drift:.2f}%")
        return 1

    print(
        f"\nOK: table 6 reconstructs table 1 everywhere within {a.tolerance}%."
        "\n    Kişi Başı columns are TL, not Bin TL. Şube Sayısı x Şubeye Düşen Nüfus"
        "\n    is the province population."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
