#!/usr/bin/env python3
"""Run the fixed-snapshot published three-turn regression contract."""

from __future__ import annotations

import json
from pathlib import Path

from kkb_agent.regression import compare_contracts, run_published_regression

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "regression" / "published_three_turn_snapshot.json"
EXPECTED = ROOT / "tests" / "fixtures" / "regression" / "published_three_turn_expected.json"


def main() -> int:
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    actual = run_published_regression(FIXTURE)
    differences = compare_contracts(expected, actual)
    print("Regression: published-three-turn")
    print(f"Snapshot: {actual['snapshot']}")
    if differences:
        for difference in differences:
            print(f"FAIL {difference.path}")
            print(f"  expected: {difference.expected!r}")
            print(f"  actual:   {difference.actual!r}")
            if difference.absolute_difference is not None:
                print(f"  absolute difference: {difference.absolute_difference!r}")
                print(f"  allowed tolerance:   {difference.allowed_tolerance!r}")
        print("Result: FAIL")
        return 1
    for label in (
        "spine + versions",
        "Turn 1 columns + findings",
        "Turn 2 deflation + lineage",
        "Turn 3 HPI + finding revision",
    ):
        print(f"PASS {label}")
    print("Result: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
