#!/usr/bin/env python3
"""SCRUM-44 - run the first published question end to end and print the answer.

    .venv/bin/python scripts/run_turn1.py
    .venv/bin/python scripts/run_turn1.py --table 8

Every figure printed here is hand-checkable against the gold layer: the frame carries the
series it came from, and --table prints the rows the findings are computed over.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.agent.turn1 import build_turn1  # noqa: E402

GOLD = ROOT / "data" / "gold" / "lakehouse.duckdb"

QUESTION = "Konut kredisi faizleri düştüğü halde kredi hacmi neden artmadı?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=GOLD)
    ap.add_argument("--table", type=int, default=0, help="print the last N rows of the frame")
    a = ap.parse_args()

    if not a.db.exists():
        print(f"{a.db} not found. Run scripts/build_catalog.py first.")
        return 1

    result = build_turn1(a.db)
    frame = result.frame

    print(f"soru : {QUESTION}\n")
    print("çözümlenen seriler")
    for resolution in result.resolutions:
        concept = resolution.concept
        print(f"   {concept.label[:44]:<44} [{concept.source}]  {resolution.series_ids[0]}")
    print()

    print(f"tablo: {len(frame.columns)} sütun x {len(frame.spine.values)} dönem")
    for column in frame.columns:
        unit = f"{column.unit.symbol} x{column.unit.scale:,.0f}" if column.unit else "—"
        print(f"   {column.key[:46]:<46} {unit:<22} eksik={column.missing_count}")
    print()

    print("hesaplanan kanıt")
    for item in result.evidence:
        print(f"   {item}")
    print()

    print("bulgular")
    for finding in frame.findings:
        print(f"   [{finding.finding_id}] {finding.statement}")
        for caveat in finding.caveats:
            print(f"        - {caveat}")
        print()

    if result.caveats:
        print("açıklanması gerekenler")
        for caveat in result.caveats:
            print(f"   ! {caveat}")
        print()

    if a.table:
        keys = [c.key for c in frame.columns]
        print("son satırlar")
        print("   dönem     " + "".join(f"{k[:18]:>20}" for k in keys))
        for index in range(max(0, len(frame.spine.values) - a.table), len(frame.spine.values)):
            cells = ""
            for column in frame.columns:
                value = column.values[index]
                cells += f"{value:>20,.2f}" if value is not None else f"{'—':>20}"
            print(f"   {frame.spine.values[index]}{cells}")
        print()

    print(f"süre : {result.elapsed_seconds:.2f}s   frame v{frame.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
