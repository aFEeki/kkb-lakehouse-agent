#!/usr/bin/env python3
"""SCRUM-97 - acquire BDDK FinTürk (İllere Göre) into the bronze layer.

The third BDDK source the organizers require. Province-level banking data.

    POST /BultenFinturk/tr/Home/VeriGetir
    data: tabloNo, donem, tarafList[i], sehirList[i]

Both list parameters are arrays, and the city list accepts the sentinel `HEPSİ`, which
returns all 82 provinces in one response. Passing every sector scope at the same time
collapses the whole acquisition to one request per (table, period) - roughly 154 calls
rather than the ~12,000 a naive province-by-province loop would make.

FinTürk is **quarterly**. Periods are `2025-12`, `2025-9`, `2025-3` and so on. Do not
resample this into months it does not have.

Usage
-----
    .venv/bin/python scripts/crawl_bddk_finturk.py --dry-run
    .venv/bin/python scripts/crawl_bddk_finturk.py --max-periods 2
    .venv/bin/python scripts/crawl_bddk_finturk.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import truststore

# bddk.org.tr's chain cannot be completed by certifi; the OS trust store can.
# Verification stays ON. Do not "fix" a TLS error here with verify=False.
SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bronze" / "bddk" / "finturk"
MANIFEST = ROOT / "data" / "bronze" / "bddk" / "manifest_finturk.jsonl"

BASE = "https://www.bddk.org.tr"
LANDING = f"{BASE}/BultenFinTurk"
ENDPOINT = f"{BASE}/BultenFinturk/tr/Home/VeriGetir"
UA = "Mozilla/5.0 (compatible; KKB-Hackathon-2026-Research/1.0)"

ALL_CITIES = "HEPSİ"  # sentinel that returns every province in one response
START, END = (2021, 1), (2026, 6)


def options(html: str, select_id: str) -> list[tuple[str, str]]:
    m = re.search(rf'id="{select_id}".*?</select>', html, re.S)
    if not m:
        return []
    out = []
    for v, t in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(0), re.S):
        label = re.sub(r"&#(\d+);", lambda x: chr(int(x.group(1))), re.sub("<[^>]+>", " ", t))
        out.append((v, " ".join(label.split())))
    return out


def in_range(donem: str) -> bool:
    """`2025-12` -> True when the quarter falls inside the requested window."""
    try:
        y, m = (int(x) for x in donem.split("-"))
    except ValueError:
        return False
    return START <= (y, m) <= END


def record(entry: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--max-periods", type=int, default=0, help="0 = all in range")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-consecutive-failures", type=int, default=5)
    a = p.parse_args()

    client = httpx.Client(
        timeout=httpx.Timeout(180.0),
        follow_redirects=True,
        verify=SSL_CTX,
        headers={"User-Agent": UA, "Referer": LANDING, "X-Requested-With": "XMLHttpRequest"},
    )

    try:
        landing = client.get(LANDING).text
    except Exception as e:
        print(f"Cannot reach {LANDING}: {type(e).__name__}: {e}")
        return 1

    tables = options(landing, "ddlTablo")
    taraflar = options(landing, "ddlTaraf")
    periods = [(v, lbl) for v, lbl in options(landing, "ddlDonem") if in_range(v)]

    print(f"tables    : {len(tables)}  {[t[1][:26] for t in tables][:3]}...")
    print(f"taraf     : {len(taraflar)}  {[t[1] for t in taraflar][:4]}...")
    print(
        f"periods   : {len(periods)} quarterly in range  "
        f"({periods[-1][0] if periods else '?'} .. {periods[0][0] if periods else '?'})"
    )
    print(f"cities    : requested as '{ALL_CITIES}' - all provinces in one response")
    print(f"output    : {OUT.relative_to(ROOT)}")

    if not tables or not periods or not taraflar:
        print("Could not discover the selectors; aborting rather than guessing.")
        return 1

    if a.max_periods:
        periods = periods[: a.max_periods]

    jobs = [(t, d) for d, _ in periods for t, _ in tables]
    todo = [(t, d) for t, d in jobs if not (OUT / d / f"tablo{t}.json").exists()]
    print(
        f"requests  : {len(todo)} to fetch of {len(jobs)}  "
        f"(~{len(todo) * (a.delay + 1.0) / 60:.0f} min)"
    )

    if a.dry_run:
        print("\n--dry-run: no requests made")
        for t, d in todo[:6]:
            print(f"    tablo {t}  donem {d}")
        if len(todo) > 6:
            print(f"    ... and {len(todo) - 6} more")
        return 0

    taraf_codes = [v for v, _ in taraflar]
    ok = failed = 0
    consecutive = 0
    t0 = time.time()

    for i, (tablo, donem) in enumerate(todo, 1):
        dest = OUT / donem / f"tablo{tablo}.json"
        payload = {"tabloNo": tablo, "donem": donem, "sehirList[0]": ALL_CITIES}
        for k, code in enumerate(taraf_codes):
            payload[f"tarafList[{k}]"] = code
        try:
            r = client.post(ENDPOINT, data=payload)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            doc = r.json()
            j = doc.get("Json") or {}
            rows = j.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("rows", [])
            if not rows:
                print(f"  [{i}/{len(todo)}] tablo {tablo} {donem}: no rows")

            raw = r.content
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            record(
                {
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "url": ENDPOINT,
                    "params": {
                        "tabloNo": tablo,
                        "donem": donem,
                        "sehirList": ALL_CITIES,
                        "tarafList": taraf_codes,
                    },
                    "path": str(dest.relative_to(ROOT)),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "col_names": j.get("colNames"),
                    "row_count": len(rows),
                    "status": "ok",
                }
            )
            ok += 1
            consecutive = 0
            if i % 20 == 0 or i <= 3:
                print(
                    f"  [{i}/{len(todo)}] tablo {tablo} {donem}  {len(rows)} rows  "
                    f"{(time.time() - t0) / 60:.0f} min"
                )
        except Exception as e:
            failed += 1
            consecutive += 1
            print(
                f"  [{i}/{len(todo)}] tablo {tablo} {donem} FAILED "
                f"{type(e).__name__}: {str(e)[:70]}"
            )
            record(
                {
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "tabloNo": tablo,
                    "donem": donem,
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}"[:300],
                }
            )
            if consecutive >= a.max_consecutive_failures:
                print("\nABORTING: repeated failures. Re-run to resume.")
                break
            time.sleep(a.delay * (2**consecutive))
            continue
        time.sleep(a.delay)

    print(f"\nok={ok} failed={failed} in {(time.time() - t0) / 60:.1f} min")
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
