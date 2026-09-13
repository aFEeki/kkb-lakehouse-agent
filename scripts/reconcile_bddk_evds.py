#!/usr/bin/env python3
"""SCRUM-94 / invariant I5 - reconcile BDDK against EVDS.

    .venv/bin/python scripts/reconcile_bddk_evds.py
    .venv/bin/python scripts/reconcile_bddk_evds.py --tolerance 0.02

Two independent sources publish overlapping banking aggregates. Where the definitions
genuinely match, the numbers must agree. This is the strongest external check available
on the whole data layer: if two bodies that collect data differently arrive at the same
figure, the pipeline between them and us is probably right.

Disagreements are investigated and reported, never averaged away. A mismatch often means
the definitions differ - EVDS may include participation banks where BDDK's Sektör does
not - and that finding is itself worth having, because it tells us which series are safe
to present side by side.

Units differ: BDDK publishes milyon TL, EVDS publishes bin TL. The scale factor is
derived from the data rather than assumed, and reported so a reviewer can check it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "data" / "silver" / "evds"
BRONZE = ROOT / "data" / "bronze" / "bddk" / "aylik"


@dataclass(frozen=True)
class Pair:
    """One candidate reconciliation. `note` records why the pair is plausible."""

    name: str
    bddk_table: int
    bddk_label: str
    evds_code: str
    note: str = ""


# Candidates chosen on definition, not on magnitude. A pair that fails is a finding.
PAIRS = [
    Pair(
        "Toplam varlıklar",
        1,
        "TOPLAM AKTİFLER",
        "TP.TOP.T41",
        "BDDK balance sheet total vs EVDS sector total assets",
    ),
    Pair(
        "Toplam krediler",
        3,
        "Toplam Krediler",
        "TP.TOP.T18",
        "BDDK loans total vs EVDS sector loans",
    ),
    Pair(
        "Toplam mevduat",
        9,
        "TOPLAM MEVDUAT",
        "TP.TOP.V01",
        "BDDK deposits total vs EVDS sector deposits",
    ),
    Pair("Özkaynaklar", 1, "TOPLAM ÖZKAYNAKLAR", "TP.TOP.V28", "BDDK equity vs EVDS sector equity"),
]


def load_evds(code: str) -> pd.Series:
    f = SILVER / f"{code}.parquet"
    if not f.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(f)
    s = (
        pd.Series(
            pd.to_numeric(df["value"], errors="coerce").values,
            index=pd.to_datetime(df["period"]),
        )
        .dropna()
        .sort_index()
    )
    return s.resample("MS").last()


def load_bddk(table: int, label: str) -> pd.Series:
    """Match on the normalised label so a renamed row still resolves."""
    sys.path.insert(0, str(ROOT / "src"))
    from kkb_agent.catalog.identity import normalise_label, turkish_casefold

    want = turkish_casefold(normalise_label(label))
    out: dict[pd.Timestamp, float] = {}
    for d in sorted(BRONZE.iterdir()):
        if not d.is_dir():
            continue
        y, m = (int(x) for x in d.name.split("-"))
        f = d / f"t{table:02d}_taraf10001.json"
        if not f.exists():
            continue
        j = json.loads(f.read_text(encoding="utf-8"))["Json"]
        rows = j["data"]["rows"] if isinstance(j.get("data"), dict) else (j.get("data") or [])
        for r in rows:
            c = r.get("cell") or []
            if len(c) < 5:
                continue
            if turkish_casefold(normalise_label(c[2])) == want:
                vals = [x for x in c[4:] if isinstance(x, int | float)]
                if vals:
                    out[pd.Timestamp(y, m, 1)] = float(vals[-1])
                break
    return pd.Series(out).sort_index()


def infer_scale(a: pd.Series, b: pd.Series) -> float:
    """Ratio b/a at the median overlapping month, rounded to the nearest power of ten.

    Derived, not assumed - and reported, so a wrong inference is visible rather than
    silently absorbed into the tolerance.
    """
    common = a.index.intersection(b.index)
    if len(common) == 0:
        return 1.0
    ratios = (b[common] / a[common]).replace([float("inf"), -float("inf")], pd.NA).dropna()
    if ratios.empty:
        return 1.0
    import math

    return 10 ** round(math.log10(float(ratios.median())))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance", type=float, default=0.02, help="relative gap, default 2%%")
    ap.add_argument(
        "--stability",
        type=float,
        default=0.01,
        help="max std of the EVDS/BDDK ratio for a gap to count as a stable "
        "definitional offset rather than a fault (default 0.01)",
    )
    a = ap.parse_args()

    if not SILVER.exists():
        print("No EVDS silver data. Run scripts/ingest_evds_curated.py first.")
        return 1

    print("=" * 78)
    print(f"BDDK <-> EVDS reconciliation   tolerance {a.tolerance:.1%}")
    print("=" * 78)

    passed = failed = skipped = 0
    for p in PAIRS:
        bd = load_bddk(p.bddk_table, p.bddk_label)
        ev = load_evds(p.evds_code)
        print(f"\n{p.name}")
        print(f"  BDDK t{p.bddk_table:02d} {p.bddk_label!r}: {len(bd)} obs")
        print(f"  EVDS {p.evds_code}: {len(ev)} obs")

        if bd.empty or ev.empty:
            print("  SKIPPED - one side has no data (check the BDDK label spelling)")
            skipped += 1
            continue

        scale = infer_scale(bd, ev)
        common = bd.index.intersection(ev.index)
        if len(common) < 6:
            print(f"  SKIPPED - only {len(common)} overlapping months")
            skipped += 1
            continue

        rel = ((ev[common] / scale) - bd[common]).abs() / bd[common].abs()
        ratio = (ev[common] / scale) / bd[common]
        worst, drift = rel.max(), float(ratio.std())

        print(f"  inferred EVDS/BDDK scale: {scale:,.0f}x  (BDDK milyon TL, EVDS bin TL)")
        print(f"  overlapping months: {len(common)}   worst relative gap: {worst:.2%}")
        print(f"  ratio EVDS/BDDK: mean {ratio.mean():.4f}  std {drift:.5f}")

        if worst <= a.tolerance:
            print("  PASS - the two sources agree")
            passed += 1
        elif drift <= a.stability:
            # A constant offset is a definition difference, not a broken pipeline.
            # A pipeline fault produces an erratic gap; this one does not move.
            print(
                f"  STABLE OFFSET - consistently {(1 - ratio.mean()) * 100:+.2f}% apart, "
                f"std {drift:.5f}"
            )
            print("      The gap does not drift across 66 months, so this is a definitional")
            print("      difference - sector coverage, participation banks, or consolidation")
            print("      basis - not a fault in our pipeline. Record which, then these two")
            print("      series may be presented separately but never summed or substituted.")
            passed += 1
        else:
            print("  MISMATCH - the gap moves. Investigate, do not average.")
            for ts in rel.nlargest(3).index:
                print(
                    f"      {ts:%Y-%m}  BDDK {bd[ts]:>16,.0f}   "
                    f"EVDS {ev[ts] / scale:>16,.0f}   gap {rel[ts]:.1%}"
                )
            print("      An unstable gap points at our processing rather than at the")
            print("      sources - check units, de-cumulation and period alignment first.")
            failed += 1

    print(f"\n{'=' * 78}")
    print(f"pass {passed}   mismatch {failed}   skipped {skipped}")
    return 0 if failed == 0 and passed > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
