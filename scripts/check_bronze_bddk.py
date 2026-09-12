#!/usr/bin/env python3
"""SCRUM-19 - health check over the BDDK bronze layer.

Repeatable version of the checks run by hand after the first crawl. Run it after every
acquisition pass, before anything downstream reads the data.

    .venv/bin/python scripts/check_bronze_bddk.py
    .venv/bin/python scripts/check_bronze_bddk.py --json   # machine-readable

Exit code is 1 when something needs a human, 0 when everything is either clean or a
schema change already acknowledged below.

What it checks
--------------
  coverage   every expected period present, no gaps, every table fetched
  integrity  file hash matches the manifest; captions match the requested period
  units      unit per table from the caption, and whether it ever changes mid-series
  drift      row-count changes, with the period and the rows added/removed
  identity   row labels whose embedded formula reference shifts over time

A dimension change is NOT automatically an error. BDDK genuinely reorganises tables when
regulation changes. Confirmed changes are listed in KNOWN_SCHEMA_CHANGES and reported as
acknowledged; anything else is reported as needing review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze" / "bddk" / "aylik"
MANIFEST = ROOT / "data" / "bronze" / "bddk" / "manifest_aylik.jsonl"

START, END = (2021, 1), (2026, 6)

# Confirmed as legitimate regulatory reorganisation, not corruption.
# (table, period) -> what changed. See SCRUM-19.
KNOWN_SCHEMA_CHANGES = {
    (3, "2022-01"): "New loan categories; Mal Karşılığı Vesaikin Finansmanı removed",
    (8, "2022-09"): "Altın Tahvili and Altına Dayalı Kira Sertifikası added",
    (12, "2021-06"): "Basel risk-weight buckets reorganised",
    (12, "2021-11"): "Risk Ağırlığı %25 and KDA Riskine Esas Tutar added",
    (12, "2022-06"): "Risk Ağırlığı %500 added",
}

# Row labels embed references to other row numbers, which shift when rows are inserted.
# Stripping them is how the same series stays identifiable across periods. See SCRUM-20.
FORMULA_SUFFIX = re.compile(r"\s*\((?:\d+(?:\s*[+\-]\s*\d+)*|\d+\s*den\s*\d+'[a-zıüşöçğ]+)\)\s*$")


def normalise(label: str) -> str:
    return FORMULA_SUFFIX.sub("", str(label)).strip()


def periods(a: tuple[int, int], b: tuple[int, int]) -> list[str]:
    out, (y, m) = [], a
    while (y, m) <= b:
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def load(path: Path) -> tuple[dict, list]:
    j = json.loads(path.read_text(encoding="utf-8"))["Json"]
    rows = j["data"]["rows"] if isinstance(j.get("data"), dict) else (j.get("data") or [])
    return j, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = ap.parse_args()

    if not BRONZE.exists():
        print(f"No bronze data at {BRONZE.relative_to(ROOT)}. Run the crawler first.")
        return 1

    expected = periods(START, END)
    found = sorted(p.name for p in BRONZE.iterdir() if p.is_dir())
    problems: list[str] = []
    report: dict = {}

    # ---- coverage -------------------------------------------------------
    missing = [p for p in expected if p not in found]
    extra = [p for p in found if p not in expected]
    tables_per_period = {
        p: sorted(int(f.stem[1:3]) for f in (BRONZE / p).glob("t*.json")) for p in found
    }
    all_tables = sorted({t for ts in tables_per_period.values() for t in ts})
    incomplete = {
        p: sorted(set(all_tables) - set(ts))
        for p, ts in tables_per_period.items()
        if set(ts) != set(all_tables)
    }

    report["coverage"] = {
        "expected_periods": len(expected),
        "found_periods": len(found),
        "missing_periods": missing,
        "unexpected_periods": extra,
        "tables": all_tables,
        "incomplete_periods": incomplete,
    }
    if missing:
        problems.append(f"{len(missing)} period(s) missing: {missing[:6]}")
    if incomplete:
        problems.append(
            f"{len(incomplete)} period(s) missing tables: {dict(list(incomplete.items())[:3])}"
        )

    # ---- manifest integrity --------------------------------------------
    mismatches, mf_errors = [], 0
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("status") != "ok":
                mf_errors += 1
                continue
            f = ROOT / e["path"]
            if not f.exists():
                mismatches.append(f"{e['path']}: in manifest, not on disk")
            elif hashlib.sha256(f.read_bytes()).hexdigest() != e["sha256"]:
                mismatches.append(f"{e['path']}: sha256 mismatch")
    report["integrity"] = {"hash_mismatches": mismatches, "manifest_non_ok": mf_errors}
    if mismatches:
        problems.append(f"{len(mismatches)} file(s) do not match the manifest")

    # ---- captions, units, drift, identity -------------------------------
    units: dict[int, Counter] = defaultdict(Counter)
    rowcounts: dict[int, dict[str, int]] = defaultdict(dict)
    labels: dict[int, dict[str, list[str]]] = defaultdict(dict)
    caption_mismatch = []
    no_period_in_caption: set[int] = set()

    for p in found:
        y, m = p.split("-")
        for f in sorted((BRONZE / p).glob("t*_taraf10001.json")):
            t = int(f.stem[1:3])
            j, rows = load(f)
            cap = j.get("caption") or ""
            # t15-t17 return a bare table name with neither unit nor period, so there is
            # nothing in the payload to verify the period against. Only check where the
            # source actually states it.
            if "Dönem:" in cap.replace(" ", ""):
                if f"Dönem:{int(y)}/{int(m)}" not in cap.replace(" ", ""):
                    caption_mismatch.append(f"{p} t{t:02d}: caption says {cap!r}")
            else:
                no_period_in_caption.add(t)
            unit = cap.split("(")[-1].split(")")[0].strip() if "(" in cap else None
            units[t][unit or "—"] += 1
            rowcounts[t][p] = len(rows)
            labels[t][p] = [str(r["cell"][2]) for r in rows]

    report["captions"] = {
        "period_mismatches": caption_mismatch,
        "tables_without_period_in_caption": sorted(no_period_in_caption),
    }
    if caption_mismatch:
        problems.append(
            f"{len(caption_mismatch)} file(s) returned a different period than requested"
        )

    report["units"] = {f"t{t:02d}": dict(c) for t, c in sorted(units.items())}
    mixed = [t for t, c in units.items() if len(c) > 1]
    if mixed:
        problems.append(f"unit changes mid-series in table(s): {mixed}")

    drift_known, drift_new = [], []
    for t in sorted(rowcounts):
        seq = sorted(rowcounts[t])
        for prev, cur in zip(seq, seq[1:], strict=False):
            if rowcounts[t][cur] == rowcounts[t][prev]:
                continue
            before, after = set(labels[t][prev]), set(labels[t][cur])
            item = {
                "table": t,
                "period": cur,
                "rows": f"{rowcounts[t][prev]} -> {rowcounts[t][cur]}",
                "added": sorted(after - before),
                "removed": sorted(before - after),
            }
            if (t, cur) in KNOWN_SCHEMA_CHANGES:
                item["acknowledged"] = KNOWN_SCHEMA_CHANGES[(t, cur)]
                drift_known.append(item)
            else:
                drift_new.append(item)
    report["drift"] = {"acknowledged": drift_known, "needs_review": drift_new}
    if drift_new:
        problems.append(
            f"{len(drift_new)} unacknowledged schema change(s) — "
            "review and add to KNOWN_SCHEMA_CHANGES"
        )

    renamed = []
    for t in sorted(labels):
        by_norm: dict[str, set[str]] = defaultdict(set)
        for p in labels[t]:
            for lab in labels[t][p]:
                by_norm[normalise(lab)].add(lab)
        for norm, variants in by_norm.items():
            if len(variants) > 1:
                renamed.append({"table": t, "normalised": norm, "variants": sorted(variants)})
    report["identity"] = {"labels_with_shifting_formula": renamed}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if problems else 0

    # ---- human-readable -------------------------------------------------
    c = report["coverage"]
    print("=" * 70)
    print(f"BDDK bronze health check   {BRONZE.relative_to(ROOT)}")
    print("=" * 70)
    print(
        f"\nCOVERAGE   {c['found_periods']}/{c['expected_periods']} periods, "
        f"{len(c['tables'])} tables  ({found[0]} .. {found[-1]})"
    )
    print(f"           missing: {missing or 'none'}")
    if incomplete:
        for p, ts in list(incomplete.items())[:5]:
            print(f"           {p} missing tables {ts}")

    print(f"\nINTEGRITY  hash mismatches: {len(mismatches)}   non-ok manifest entries: {mf_errors}")
    for m in mismatches[:5]:
        print(f"           ! {m}")
    print(
        f"           captions matching requested period: "
        f"{'all' if not caption_mismatch else f'{len(caption_mismatch)} MISMATCH'}"
    )
    if no_period_in_caption:
        print(f"           tables {sorted(no_period_in_caption)} state no period in the caption —")
        print("           for these the only evidence of the period is our own request,")
        print("           so the manifest params are the provenance record. See SCRUM-13.")

    print("\nUNITS")
    for t, cc in sorted(units.items()):
        flag = "   <-- CHANGES MID-SERIES" if len(cc) > 1 else ""
        print(f"           t{t:02d}  {dict(cc)}{flag}")

    print(f"\nSCHEMA DRIFT   {len(drift_known)} acknowledged, {len(drift_new)} need review")
    for d in drift_known:
        print(f"           ok  t{d['table']:02d} {d['period']}  {d['rows']}  ({d['acknowledged']})")
    for d in drift_new:
        print(f"           !!  t{d['table']:02d} {d['period']}  {d['rows']}")
        for a in d["added"][:4]:
            print(f"                 + {a[:64]}")
        for r in d["removed"][:4]:
            print(f"                 - {r[:64]}")

    print(f"\nIDENTITY   {len(renamed)} label(s) whose embedded formula shifts over time")
    for r in renamed[:8]:
        print(f"           t{r['table']:02d}  {r['normalised'][:46]}")
        for v in r["variants"]:
            print(f"                 {v[:64]}")
    if renamed:
        print("           ^ display name is not a safe series key. See SCRUM-20.")

    print("\n" + "=" * 70)
    if problems:
        print("NEEDS ATTENTION")
        for p in problems:
            print(f"  - {p}")
    else:
        print("All checks pass.")
    print("=" * 70)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
