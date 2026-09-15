#!/usr/bin/env python3
"""SCRUM-27 (I2, I3) - sign and classification consistency for cumulative series.

    .venv/bin/python scripts/check_cumulative_consistency.py
    .venv/bin/python scripts/check_cumulative_consistency.py --show 20

I1 already proves that de-cumulated months add back to the published December figure. What
it cannot prove is that the series should have been de-cumulated at all: de-cumulating and
re-summing is a telescoping identity, true for any series whichever mode it was given. A
misclassification sails through I1 untouched. I2 and I3 are the checks that look at it.

**I2, sign.** Deliberately narrow, and the narrowness is the point. Measured against the
catalog, negatives are overwhelmingly legitimate: 161 flow series (net figures), 29 stock
series (FX net positions, retained losses, valuation differences), 9 ratio and 8 rate
series. Even de-cumulation legitimately produces them - 91 of 599 year-to-date series go
negative in some month because a tax or impairment provision was released. The one measure
type whose definition genuinely forbids a negative is `count`: a branch count cannot be
less than zero. So that is what this asserts, rather than a universal rule that would fire
on correct data and be switched off within a day.

**I3, classification.** Two claims, separately scoped.

First, a year-to-date series must actually show the January reset. Scoped to series that
have data: 20 series are entirely zero - income lines for a bank group that has none of
that business - and a series of zeros cannot demonstrate a reset. Of the 579 that carry
real figures, 579 show it.

Second, where the pattern and the accounting disagree, say so. `resolve_by_statement`
returns early when the pattern is confident and never consults the statement kind, on the
deliberate grounds that data contradicting the accounting is information rather than noise
- but nothing was reporting it, so the information went nowhere. This recomputes what the
statement kind would have said and lists every difference. It does not reclassify: which
of the two is right is a judgement about the source, not something a script should decide
silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.transform.cumulative import (  # noqa: E402
    BDDK_AYLIK_STATEMENT,
    INCOME_LINES_ON_BALANCE_SHEET,
    StatementKind,
)

GOLD = ROOT / "data" / "gold" / "lakehouse.duckdb"

# The share of January opportunities that must show a reset for a year-to-date series to
# be consistent with its classification. Matches the classifier's own threshold.
MIN_RESET_RATIO = 0.6

# Known disagreements between the pattern verdict and the statement kind, as measured on
# the full catalog. They are recorded rather than fixed because deciding which side is
# right means reading BDDK's own definition of each line. The check fails if the number
# grows, so a code change that introduces new ones cannot pass quietly.
BASELINE_DISAGREEMENTS = 129


def statement_verdict(table_no: int, label: str) -> str | None:
    """What the accounting alone would say about this row."""
    if str(label).strip().casefold() in INCOME_LINES_ON_BALANCE_SHEET:
        return "ytd"
    kind = BDDK_AYLIK_STATEMENT.get(table_no)
    if kind is None:
        return None
    return "ytd" if kind is StatementKind.INCOME_STATEMENT else "none"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=GOLD)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument(
        "--max-disagreements",
        type=int,
        default=BASELINE_DISAGREEMENTS,
        help="fail if pattern/statement disagreements exceed this",
    )
    ap.add_argument(
        "--min-checked",
        type=int,
        default=1,
        help="fail if fewer series were examined (guards a vacuous pass)",
    )
    a = ap.parse_args()

    if not a.db.exists():
        print(f"{a.db} not found. Run scripts/build_catalog.py first.")
        return 1

    connection = duckdb.connect(str(a.db), read_only=True)
    failures = 0

    # ---- I2: a count cannot be negative -------------------------------------------
    counts = connection.execute(
        "SELECT count(*) FROM series_catalog WHERE measure_type = 'count'"
    ).fetchone()[0]
    negative = connection.execute(
        """
        SELECT sc.series_id, sc.name_tr, min(o.value)
        FROM series_observations o JOIN series_catalog sc USING (series_id)
        WHERE sc.measure_type = 'count' AND o.value < 0
        GROUP BY 1, 2 ORDER BY 3
        """
    ).fetchall()

    print(f"{'=' * 72}\nI2  sign, scoped to counts")
    print(f"count series        : {counts:,}")
    print(f"negative            : {len(negative):,}")
    if negative:
        failures += 1
        print("\nFAIL: a count cannot be negative")
        for series_id, name, low in negative[: a.show]:
            print(f"   {str(name)[:48]:<48} min {low:>14,.2f}   {series_id[:44]}")
    elif counts < a.min_checked:
        failures += 1
        print(f"\nFAIL: only {counts:,} count series examined; nothing was verified.")

    # ---- I3a: a year-to-date series must show the January reset ---------------------
    resets = connection.execute(
        """
        WITH ytd AS (
            SELECT series_id, name_tr, nonzero_observations
            FROM series_catalog WHERE cumulative_mode = 'ytd'
        ),
        observed AS (
            SELECT o.series_id, o.period, o.value_reported AS v
            FROM series_observations o JOIN ytd USING (series_id)
        ),
        jan AS (SELECT * FROM observed WHERE month(period) = 1),
        dec AS (SELECT * FROM observed WHERE month(period) = 12)
        SELECT jan.series_id,
               any_value(ytd.name_tr),
               any_value(ytd.nonzero_observations),
               sum(CASE WHEN dec.v > 0 AND jan.v <= dec.v * 0.5 THEN 1 ELSE 0 END),
               count(*)
        FROM jan
        JOIN dec ON dec.series_id = jan.series_id
                AND year(dec.period) = year(jan.period) - 1
        JOIN ytd ON ytd.series_id = jan.series_id
        GROUP BY 1
        """
    ).fetchall()

    # A series of zeros has nothing to reset. These are income lines for a bank group with
    # none of that business, and excluding them is scoping, not waiving.
    with_data = [r for r in resets if r[2] > 0]
    missing = [r for r in with_data if r[4] and r[3] / r[4] < MIN_RESET_RATIO]

    print(f"\n{'=' * 72}\nI3a year-to-date series show the January reset")
    print(f"ytd series          : {len(resets):,}")
    print(f"empty, not testable : {len(resets) - len(with_data):,}")
    print(f"tested              : {len(with_data):,}")
    print(f"without the reset   : {len(missing):,}")
    if missing:
        failures += 1
        print("\nFAIL: classified year-to-date but the data does not reset in January")
        for series_id, name, _, hits, chances in missing[: a.show]:
            print(f"   {str(name)[:48]:<48} {hits}/{chances}   {series_id[:40]}")
    elif len(with_data) < a.min_checked:
        failures += 1
        print(f"\nFAIL: only {len(with_data):,} testable series; nothing was verified.")

    # ---- I3b: report where the pattern and the accounting disagree ------------------
    rows = connection.execute(
        """
        SELECT series_id, name_tr, source_ref, cumulative_mode, nonzero_observations
        FROM series_catalog WHERE source = 'bddk_aylik'
        """
    ).fetchall()

    disagreements = []
    for series_id, name, source_ref, mode, nonzero in rows:
        match = re.match(r"t(\d+)", source_ref or "")
        if not match:
            continue
        verdict = statement_verdict(int(match.group(1)), name)
        if verdict and verdict != str(mode):
            disagreements.append((series_id, name, source_ref, str(mode), verdict, nonzero))

    substantive = [d for d in disagreements if d[5] > 0]
    print(f"\n{'=' * 72}\nI3b pattern against the accounting")
    print(f"aylik series        : {len(rows):,}")
    print(f"disagreements       : {len(disagreements):,}  ({len(substantive):,} carrying data)")
    print(f"baseline            : {a.max_disagreements:,}")

    for _series_id, name, source_ref, mode, verdict, nonzero in substantive[: a.show]:
        print(
            f"   {str(name)[:44]:<44} {source_ref:<11} stored {mode:<5} "
            f"accounting {verdict:<5} n={nonzero}"
        )

    if len(disagreements) > a.max_disagreements:
        failures += 1
        print(
            f"\nFAIL: {len(disagreements):,} disagreements, above the recorded baseline of "
            f"{a.max_disagreements:,}. A change has introduced new ones."
        )

    print(f"\n{'=' * 72}")
    if failures:
        print(f"{failures} check(s) FAILED.")
        return 1
    print("OK: counts are non-negative, every year-to-date series with data resets in")
    print("    January, and no new pattern/accounting disagreements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
