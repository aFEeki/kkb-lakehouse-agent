#!/usr/bin/env python3
"""SCRUM-6 / SCRUM-13 - acquire BDDK monthly bulletin data into the bronze layer.

BDDK's monthly bulletin is not a file listing. It is an ASP.NET report viewer with a
JSON endpoint behind it:

    POST /BultenAylik/tr/Home/BasitRaporGetir
    data: tabloNo, yil, ay, paraBirimi, taraf

It returns structured JSON with colNames, colModels and rows - no Excel parsing needed
for this source. The caption carries the unit ("milyon TL") and the period.

Usage
-----
    # see what would be fetched, no requests at all
    .venv/bin/python scripts/crawl_bddk_aylik.py --dry-run

    # small validation run before committing to the full crawl
    .venv/bin/python scripts/crawl_bddk_aylik.py --limit 6

    # full first pass (17 tables x Sektor x 66 months)
    .venv/bin/python scripts/crawl_bddk_aylik.py

    # later: add a sector breakdown
    .venv/bin/python scripts/crawl_bddk_aylik.py --taraf 10002

Politeness
----------
Strictly sequential, never parallel. Default 2.0s between requests. Backs off on errors
and ABORTS after a run of consecutive failures rather than hammering a source we depend
on for the next eight days. Getting blocked before demo day is unrecoverable, so the
defaults here are deliberately conservative - raise them, don't lower them.

Idempotent: a file that already exists is skipped, so the crawl resumes after an abort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import truststore

# bddk.org.tr serves a chain that certifi's bundle cannot complete, so the default
# httpx verification fails while curl and browsers succeed. truststore uses the OS
# trust store, which is what they use. This keeps certificate verification ON.
# Do NOT "fix" a TLS error here with verify=False.
SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bronze" / "bddk" / "aylik"
MANIFEST = ROOT / "data" / "bronze" / "bddk" / "manifest_aylik.jsonl"

BASE = "https://www.bddk.org.tr"
LANDING = f"{BASE}/BultenAylik"
ENDPOINT = f"{BASE}/BultenAylik/tr/Home/BasitRaporGetir"

UA = "Mozilla/5.0 (compatible; KKB-Hackathon-2026-Research/1.0)"

# From the TabloListesi select on the landing page.
TABLES = {
    1: "Bilanço",
    2: "Kar Zarar",
    3: "Krediler",
    4: "Tüketici Kredileri",
    5: "Sektörel Kredi Dağılımı",
    6: "KOBİ Kredileri",
    7: "Sendikasyon Seküritizasyon Kredileri",
    8: "Menkul Kıymetler",
    9: "Mevduat Türler İtibarıyla",
    10: "Mevduat Vade İtibarıyla",
    11: "Likidite Durumu",
    12: "Sermaye Yeterliliği",
    13: "Yabancı Para Pozisyonu",
    14: "Bilanço Dışı İşlemler",
    15: "Rasyolar",
    16: "Diğer Bilgiler",
    17: "Yurt Dışı Şube Rasyoları",
}

# From the ddlTaraf select. This is the sector_scope dimension in the catalog.
TARAF = {
    10001: "Sektör",
    10002: "Mevduat",
    10003: "Katılım",
    10004: "Kalkınma ve Yatırım",
    10005: "Yerli Özel",
    10006: "Kamu",
    10007: "Yabancı",
    10008: "Mevduat-Yerli Özel",
    10009: "Mevduat-Kamu",
    10010: "Mevduat-Yabancı",
}

START = (2021, 1)
END = (2026, 6)


def periods(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out, (y, m) = [], start
    while (y, m) <= end:
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def target(yil: int, ay: int, tablo: int, taraf: int) -> Path:
    return OUT / f"{yil}-{ay:02d}" / f"t{tablo:02d}_taraf{taraf}.json"


def record(entry: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--delay", type=float, default=2.0, help="seconds between requests (default 2.0)")
    p.add_argument("--limit", type=int, default=0, help="stop after N fetches (0 = no limit)")
    p.add_argument("--taraf", type=int, action="append", default=None,
                   help="taraf code, repeatable (default 10001 Sektör only)")
    p.add_argument("--tables", type=int, nargs="*", default=None, help="table numbers (default all 17)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-consecutive-failures", type=int, default=5)
    a = p.parse_args()

    tarafs = a.taraf or [10001]
    tables = a.tables or sorted(TABLES)
    months = periods(START, END)

    jobs = [(y, m, t, tf) for (y, m) in months for t in tables for tf in tarafs]
    todo = [j for j in jobs if not target(*j).exists()]

    print(f"tables      : {len(tables)}  {[TABLES.get(t, t) for t in tables][:4]}{'...' if len(tables) > 4 else ''}")
    print(f"taraf       : {[TARAF.get(t, t) for t in tarafs]}")
    print(f"months      : {len(months)}  ({START[0]}-{START[1]:02d} .. {END[0]}-{END[1]:02d})")
    print(f"total jobs  : {len(jobs)}")
    print(f"already have: {len(jobs) - len(todo)}")
    print(f"to fetch    : {len(todo)}")
    if a.limit:
        todo = todo[: a.limit]
        print(f"limited to  : {len(todo)}")
    est = len(todo) * (a.delay + 0.4)
    print(f"est. runtime: {est/60:.1f} min at {a.delay}s delay")
    print(f"output      : {OUT.relative_to(ROOT)}")

    if a.dry_run:
        print("\n--dry-run: no requests made")
        for j in todo[:10]:
            print(f"   {j[0]}-{j[1]:02d}  t{j[2]:02d} {TABLES.get(j[2])}  taraf {j[3]}")
        if len(todo) > 10:
            print(f"   ... and {len(todo)-10} more")
        return 0

    if not todo:
        print("\nNothing to do.")
        return 0

    ok = skipped = failed = 0
    consecutive = 0
    t_start = time.time()

    with httpx.Client(
        timeout=httpx.Timeout(45.0),
        follow_redirects=True,
        verify=SSL_CTX,
        headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LANDING,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    ) as client:
        # Establish a session the way a browser would before posting.
        try:
            client.get(LANDING)
        except Exception as e:
            print(f"Could not reach the landing page: {type(e).__name__}: {e}")
            return 1

        for i, (yil, ay, tablo, taraf) in enumerate(todo, 1):
            dest = target(yil, ay, tablo, taraf)
            label = f"{yil}-{ay:02d} t{tablo:02d} {TABLES.get(tablo, '')[:24]:<24} taraf={taraf}"
            try:
                r = client.post(
                    ENDPOINT,
                    data={"tabloNo": tablo, "yil": yil, "ay": ay, "paraBirimi": "TL", "taraf": taraf},
                )
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                payload = r.json()
                if not payload.get("success"):
                    # A real "no data for this period" answer, not a transport failure.
                    print(f"  [{i}/{len(todo)}] {label}  no data: {str(payload.get('error'))[:60]}")
                    record({
                        "fetched_at": datetime.now(UTC).isoformat(), "yil": yil, "ay": ay,
                        "tabloNo": tablo, "taraf": taraf, "status": "no_data",
                        "error": str(payload.get("error"))[:200],
                    })
                    skipped += 1
                    consecutive = 0
                    time.sleep(a.delay)
                    continue

                raw = r.content
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)

                j = payload["Json"]
                rows = j["data"]["rows"] if isinstance(j.get("data"), dict) else j.get("data") or []
                record({
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "url": ENDPOINT,
                    "params": {"tabloNo": tablo, "yil": yil, "ay": ay, "paraBirimi": "TL", "taraf": taraf},
                    "path": str(dest.relative_to(ROOT)),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "http_status": r.status_code,
                    "caption": j.get("caption"),
                    "col_names": j.get("colNames"),
                    "row_count": len(rows),
                    "status": "ok",
                })
                ok += 1
                consecutive = 0
                if i % 25 == 0 or i <= 3:
                    rate = i / max(time.time() - t_start, 1e-6)
                    print(f"  [{i}/{len(todo)}] {label}  {len(rows):>3} rows  "
                          f"({rate*60:.0f}/min, {(len(todo)-i)/max(rate,1e-6)/60:.0f} min left)")

            except Exception as e:
                failed += 1
                consecutive += 1
                print(f"  [{i}/{len(todo)}] {label}  FAILED {type(e).__name__}: {str(e)[:80]}")
                record({
                    "fetched_at": datetime.now(UTC).isoformat(), "yil": yil, "ay": ay,
                    "tabloNo": tablo, "taraf": taraf, "status": "error",
                    "error": f"{type(e).__name__}: {e}"[:300],
                })
                if consecutive >= a.max_consecutive_failures:
                    print(f"\nABORTING: {consecutive} consecutive failures. "
                          "Not hammering a source we depend on. Re-run to resume.")
                    break
                time.sleep(a.delay * (2 ** consecutive))  # back off
                continue

            time.sleep(a.delay)

    mins = (time.time() - t_start) / 60
    print(f"\nok={ok}  no_data={skipped}  failed={failed}  in {mins:.1f} min")
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    if failed:
        print("Re-run the same command to retry only what is missing.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
