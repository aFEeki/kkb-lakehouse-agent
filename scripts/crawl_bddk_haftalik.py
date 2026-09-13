#!/usr/bin/env python3
"""SCRUM-16 - acquire the BDDK weekly bulletin into the bronze layer.

The organizers require all three BDDK sources (Haftalık, Aylık, FinTürk) for
2021-01 to 2026-06. This covers the weekly bulletin.

Unlike the monthly bulletin, weekly is NOT a JSON endpoint. It is a stateful
ASP.NET form flow that renders HTML tables:

    GET  /BultenHaftalik                          -> session cookie + anti-forgery token
    POST /BultenHaftalik/tr/Home/DonemDegistir    -> yil, donemId, para   (set period)
    POST /BultenHaftalik/                         -> tabloId              (set table)
    POST /BultenHaftalik/tr/Home/TarafSec         -> tarafKodu            (set sector)

Every POST needs a fresh __RequestVerificationToken scraped from the previous
response; without it the endpoint returns 500. Selection is session state, so the
period must be set before iterating tables.

We store the raw HTML per (period, table). Parsing is a separate step - bronze keeps
the untouched record we hash against later.

Usage
-----
    .venv/bin/python scripts/crawl_bddk_haftalik.py --dry-run
    .venv/bin/python scripts/crawl_bddk_haftalik.py --max-periods 2   # validate first
    .venv/bin/python scripts/crawl_bddk_haftalik.py                   # full range

Politeness matches the monthly crawler: strictly sequential, 2s delay, idempotent,
exponential backoff, and it aborts after repeated failures rather than hammering a
source we depend on.
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
OUT = ROOT / "data" / "bronze" / "bddk" / "haftalik"
MANIFEST = ROOT / "data" / "bronze" / "bddk" / "manifest_haftalik.jsonl"

BASE = "https://www.bddk.org.tr"
LANDING = f"{BASE}/BultenHaftalik"
SET_PERIOD = f"{BASE}/BultenHaftalik/tr/Home/DonemDegistir"
SET_TABLE = f"{BASE}/BultenHaftalik/"

UA = "Mozilla/5.0 (compatible; KKB-Hackathon-2026-Research/1.0)"
TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')

# Month names as BDDK writes them, for turning "Eylül/05 (36. Hafta)" into a date.
MONTHS = {
    "ocak": 1,
    "şubat": 2,
    "subat": 2,
    "mart": 3,
    "nisan": 4,
    "mayıs": 5,
    "mayis": 5,
    "haziran": 6,
    "temmuz": 7,
    "ağustos": 8,
    "agustos": 8,
    "eylül": 9,
    "eylul": 9,
    "ekim": 10,
    "kasım": 11,
    "kasim": 11,
    "aralık": 12,
    "aralik": 12,
}

START_YEAR, END_YEAR = 2021, 2026
END_MONTH = 6  # range closes 2026-06


def token_of(html: str) -> str | None:
    m = TOKEN_RE.search(html)
    return m.group(1) if m else None


def unescape(s: str) -> str:
    return re.sub(r"&#(\d+);", lambda x: chr(int(x.group(1))), s).strip()


def selected_period(html: str) -> tuple[int | None, str | None]:
    """Read back which year and week the response is actually showing.

    The response is the source of truth for what we fetched, exactly as the monthly
    crawler trusts the caption over the request.

    Note the attribute order: BDDK emits `<option value="2025" selected>`, so a regex
    expecting `selected` before `value` silently never matches.
    """
    year = None
    m = re.search(r'<option value="(\d{4})"[^>]*selected', html)
    if m:
        year = int(m.group(1))
    label = None
    m = re.search(r"<option[^>]*selected[^>]*>([^<]*Hafta[^<]*)</option>", html)
    if m:
        label = unescape(m.group(1))
    return year, label


def discover_periods(html: str) -> list[tuple[int, int, str]]:
    """All (donemId, year, label) from the landing page.

    Each period option carries its year in a CSS class - `class="Yil-2025 YilDonem"` -
    so the whole calendar is available from one request. No need to walk backwards
    probing for the range boundary.
    """
    out = []
    for m in re.finditer(
        r'<option value="(\d+)"[^>]*class="Yil-(\d{4})[^"]*"[^>]*>(.*?)</option>', html, re.S
    ):
        out.append((int(m.group(1)), int(m.group(2)), unescape(m.group(3))))
    return out


def label_to_date(year: int, label: str) -> str | None:
    """'Eylül/05 (36. Hafta)' + 2025 -> '2025-09-05'."""
    m = re.match(r"\s*([^/]+)/(\d{1,2})", label or "")
    if not m:
        return None
    mon = MONTHS.get(m.group(1).strip().lower())
    return f"{year}-{mon:02d}-{int(m.group(2)):02d}" if mon else None


def in_range(year: int | None, label: str | None) -> bool | None:
    """True in range, False out of range, None unknown."""
    if year is None:
        return None
    if year < START_YEAR:
        return False
    if year > END_YEAR:
        return False
    if year == END_YEAR and label:
        m = re.match(r"\s*([^/]+)/", label)
        mon = MONTHS.get(m.group(1).strip().lower()) if m else None
        if mon and mon > END_MONTH:
            return False
    return True


def record(entry: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def discover_tables(html: str) -> dict[str, str]:
    """tabloId -> label, from the sidebar list."""
    out = {}
    for m in re.finditer(
        r"TabloDegistir\('(\d+)'\).*?<span class=\"text\">(.*?)</span>", html, re.S
    ):
        out[m.group(1)] = " ".join(re.sub("<[^>]+>", " ", m.group(2)).split())
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--para", default="TL", choices=["TL", "USD"])
    p.add_argument("--max-periods", type=int, default=0, help="0 = all in range")
    p.add_argument("--start-donem", type=int, default=0, help="0 = newest available")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-consecutive-failures", type=int, default=5)
    a = p.parse_args()

    client = httpx.Client(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
        verify=SSL_CTX,
        headers={"User-Agent": UA, "Referer": LANDING},
    )

    try:
        landing = client.get(LANDING).text
    except Exception as e:
        print(f"Cannot reach {LANDING}: {type(e).__name__}: {e}")
        return 1

    token = token_of(landing)
    if not token:
        print("No anti-forgery token on the landing page; the flow has changed.")
        return 1

    tables = discover_tables(landing)
    all_periods = discover_periods(landing)
    periods = [p for p in all_periods if in_range(p[1], p[2]) is True]

    print(f"tables discovered : {len(tables)}  {list(tables.values())[:4]}...")
    print(f"periods on site   : {len(all_periods)}")
    print(
        f"periods in range  : {len(periods)}  "
        f"({label_to_date(*periods[-1][1:]) if periods else '?'} .. "
        f"{label_to_date(*periods[0][1:]) if periods else '?'})"
    )
    print(f"currency          : {a.para}")
    print(f"output            : {OUT.relative_to(ROOT)}")

    if not tables or not periods:
        print("Could not discover tables or periods; aborting rather than guessing.")
        return 1

    if a.max_periods:
        periods = periods[: a.max_periods]
        print(f"limited to        : {len(periods)} periods")

    todo = sum(
        1
        for d, y, lab in periods
        for t in tables
        if not (
            OUT / a.para / (label_to_date(y, lab) or f"{y}-donem{d}") / f"tablo{t}.html"
        ).exists()
    )
    print(f"files to fetch    : {todo}  (~{todo * (a.delay + 0.4) / 60:.0f} min at {a.delay}s)")

    if a.dry_run:
        print("\n--dry-run: no requests made")
        print(f"  tables: {json.dumps(tables, ensure_ascii=False)}")
        for d, y, lab in periods[:5]:
            print(f"    donem {d}  {label_to_date(y, lab)}  {lab}")
        if len(periods) > 5:
            print(f"    ... and {len(periods) - 5} more periods")
        return 0

    ok = skipped = failed = 0
    consecutive = 0
    periods_done = 0
    t_start = time.time()

    for donem, year, label in periods:
        date = label_to_date(year, label) or f"{year}-donem{donem}"
        wanted = [t for t in tables if not (OUT / a.para / date / f"tablo{t}.html").exists()]
        if not wanted:
            skipped += len(tables)
            periods_done += 1
            continue

        try:
            r = client.post(
                SET_PERIOD,
                data={
                    "__RequestVerificationToken": token,
                    "yil": str(year),
                    "donemId": str(donem),
                    "para": a.para,
                },
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} setting period")
            token = token_of(r.text) or token
            got_year, got_label = selected_period(r.text)
            # Trust the response over the request, as the monthly crawler trusts the caption.
            if got_year and got_year != year:
                raise RuntimeError(f"asked for {year}/{label!r}, got {got_year}/{got_label!r}")
        except Exception as e:
            failed += 1
            consecutive += 1
            print(f"  {date}: period switch FAILED {type(e).__name__}: {str(e)[:70]}")
            if consecutive >= a.max_consecutive_failures:
                print("\nABORTING: repeated failures. Re-run to resume.")
                break
            time.sleep(a.delay * (2**consecutive))
            continue

        consecutive = 0
        periods_done += 1
        time.sleep(a.delay)

        for tablo_id in wanted:
            tablo_label = tables[tablo_id]
            dest = OUT / a.para / date / f"tablo{tablo_id}.html"
            try:
                rt = client.post(
                    SET_TABLE,
                    data={"__RequestVerificationToken": token, "tabloId": tablo_id},
                )
                if rt.status_code != 200:
                    raise RuntimeError(f"HTTP {rt.status_code}")
                token = token_of(rt.text) or token
                raw = rt.content
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
                record(
                    {
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "url": SET_TABLE,
                        "params": {"donemId": donem, "tabloId": tablo_id, "para": a.para},
                        "period_label": label,
                        "period_date": date,
                        "year": year,
                        "table_label": tablo_label,
                        "path": str(dest.relative_to(ROOT)),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw),
                        "status": "ok",
                    }
                )
                ok += 1
                consecutive = 0
            except Exception as e:
                failed += 1
                consecutive += 1
                record(
                    {
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "donemId": donem,
                        "tabloId": tablo_id,
                        "para": a.para,
                        "status": "error",
                        "error": f"{type(e).__name__}: {e}"[:300],
                    }
                )
                if consecutive >= a.max_consecutive_failures:
                    print("\nABORTING: repeated failures. Re-run to resume.")
                    return 1
                time.sleep(a.delay * (2**consecutive))
            time.sleep(a.delay)

        if periods_done % 5 == 0 or periods_done <= 2:
            el = time.time() - t_start
            print(
                f"  [{periods_done}] {date} ({label})  ok={ok} skipped={skipped} "
                f"failed={failed}  {el / 60:.0f} min elapsed"
            )

    mins = (time.time() - t_start) / 60
    print(f"\nperiods={periods_done} ok={ok} skipped={skipped} failed={failed} in {mins:.1f} min")
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
