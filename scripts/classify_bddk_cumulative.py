#!/usr/bin/env python3
"""SCRUM-93 - classify every BDDK monthly series by cumulative mode.

    .venv/bin/python scripts/classify_bddk_cumulative.py
    .venv/bin/python scripts/classify_bddk_cumulative.py --csv out.csv

Answers the question the de-cumulation gate was built on but nobody had checked against
real data: are any BDDK monthly series actually cumulative?

Writes a review table. Classification proposes; a person confirms and records the
verdict in the catalog with their initials.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kkb_agent.transform.cumulative import CumulativeMode, classify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze" / "bddk" / "aylik"

FORMULA_SUFFIX = re.compile(r"\s*\((?:\d+(?:\s*[+\-]\s*\d+)*|\d+\s*den\s*\d+'[a-zıüşöçğ]+)\)\s*$")


def normalise(label: str) -> str:
    return FORMULA_SUFFIX.sub("", str(label)).strip()


def load_series() -> dict[tuple[int, str], pd.Series]:
    """(tabloNo, normalised row label) -> monthly series of the Toplam column."""
    frames: dict[tuple[int, str], dict] = {}
    for period_dir in sorted(BRONZE.iterdir()):
        if not period_dir.is_dir():
            continue
        y, m = (int(x) for x in period_dir.name.split("-"))
        ts = pd.Timestamp(year=y, month=m, day=1)
        for f in sorted(period_dir.glob("t*_taraf10001.json")):
            tablo = int(f.stem[1:3])
            j = json.loads(f.read_text(encoding="utf-8"))["Json"]
            rows = j["data"]["rows"] if isinstance(j.get("data"), dict) else (j.get("data") or [])
            # The last numeric cell is the total for every table shape BDDK serves,
            # so it works across the 7- and 13-column layouts without a per-table map.
            for r in rows:
                cell = r.get("cell") or []
                if len(cell) < 5:
                    continue
                label = normalise(cell[2])
                val = None
                for c in reversed(cell[4:]):
                    if isinstance(c, int | float):
                        val = float(c)
                        break
                if val is None:
                    continue
                frames.setdefault((tablo, label), {})[ts] = val
    return {k: pd.Series(v).sort_index() for k, v in frames.items() if len(v) >= 13}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args()

    if not BRONZE.exists():
        print(f"No bronze data at {BRONZE.relative_to(ROOT)}. Run the crawler first.")
        return 1

    series = load_series()
    print(f"series loaded: {len(series)}  (Sektör, Toplam column, >=13 observations)\n")

    results = []
    for (tablo, label), s in sorted(series.items()):
        ev = classify(s)
        results.append((tablo, label, ev, s))

    counts = Counter(ev.mode for _, _, ev, _ in results)
    print("=" * 74)
    print("CLASSIFICATION")
    print("=" * 74)
    for mode in (
        CumulativeMode.YTD,
        CumulativeMode.INCEPTION,
        CumulativeMode.AMBIGUOUS,
        CumulativeMode.NONE,
    ):
        print(f"  {str(mode):<12} {counts.get(mode, 0):>4}")

    ytd = [r for r in results if r[2].mode is CumulativeMode.YTD]
    print(f"\n{'=' * 74}\nYEAR-TO-DATE CANDIDATES ({len(ytd)})\n{'=' * 74}")
    if not ytd:
        print("  none — no series in the monthly bulletin resets each January")
    for tablo, label, ev, _ in ytd[: a.show]:
        print(f"  t{tablo:02d}  {label[:48]:<48} {ev.summary()[:60]}")

    amb = [r for r in results if r[2].mode is CumulativeMode.AMBIGUOUS]
    print(f"\n{'=' * 74}\nAMBIGUOUS — needs the source definition ({len(amb)})\n{'=' * 74}")
    for tablo, label, ev, _ in amb[: a.show]:
        print(f"  t{tablo:02d}  {label[:48]:<48} monotone={ev.globally_monotone}")
    if len(amb) > a.show:
        print(f"  ... and {len(amb) - a.show} more")

    print(f"\n{'=' * 74}\nVERDICT\n{'=' * 74}")
    if not ytd:
        print("No year-to-date series found in the monthly bulletin.")
        print("Every series either falls in some period (an ordinary stock or flow) or")
        print("rises smoothly without ever resetting.")
        print()
        print("The organizers state that some BDDK data is cumulative. If none of it is")
        print("here, it is likely in the weekly bulletin or FinTürk. Check those before")
        print("concluding the warning does not apply.")
    else:
        print(f"{len(ytd)} series reset each January and accumulate within the year.")
        print("These require de-cumulation before any period-over-period figure is derived.")
    print()
    print(f"{len(amb)} series cannot be separated by pattern alone. A smooth non-resetting")
    print("series is an ordinary nominal stock OR inception-cumulative, and under")
    print("2021-2026 Turkish inflation both look identical. Source definition decides.")

    if a.csv:
        with a.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "tabloNo",
                    "label",
                    "mode",
                    "january_resets",
                    "january_opportunities",
                    "within_year_monotone_years",
                    "years",
                    "globally_monotone",
                    "observations",
                    "notes",
                ]
            )
            for tablo, label, ev, s in results:
                w.writerow(
                    [
                        tablo,
                        label,
                        ev.mode,
                        ev.january_resets,
                        ev.january_opportunities,
                        ev.within_year_monotone_years,
                        ev.years_observed,
                        ev.globally_monotone,
                        len(s),
                        "; ".join(ev.notes),
                    ]
                )
        print(f"\nreview table: {a.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
