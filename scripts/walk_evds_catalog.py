#!/usr/bin/env python3
"""SCRUM-99 - walk the EVDS metadata tree instead of hand-listing series codes.

    .venv/bin/python scripts/walk_evds_catalog.py              # full walk, cached
    .venv/bin/python scripts/walk_evds_catalog.py --search kullandır
    .venv/bin/python scripts/walk_evds_catalog.py --refresh    # ignore the cache

Why a walk and not a list
-------------------------
The first EVDS pass hand-picked 22 codes. That unblocked the demo but it cannot be
checked, cannot be re-run, and cannot answer "is there a series for X?" with anything
better than "I did not find one". Walking the published tree makes the selection
reproducible and lets an absence be stated from a search rather than from memory.

Three endpoints, all under the same base as the data API, all taking parameters as
path segments rather than a query string:

    /categories/type=json                    154 topic categories
    /datagroups/mode=0&type=json             678 data groups, one per published table
    /serieList/type=json&code=<datagroup>    the series inside one group

The unit is on the DATAGROUP, not the series
--------------------------------------------
`BIRIMI` is a datagroup field. That is why every ingested EVDS series had an empty unit
and failed is_usable(): we were looking for it on the series, where it does not exist.

The series record carries something better than a guessed aggregation rule, too:
SUMABLE / AVGABLE / LASTABLE and DEFAULT_AGG_METHOD. EVDS states outright whether a
series may be summed. Nothing needs to be inferred from the name.

Output
------
data/bronze/evds/metadata/{categories,datagroups}.json and serielist/<code>.json, plus
a flattened index.json of every series with its datagroup unit attached. Cached: a
second run costs no requests.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import truststore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bronze" / "evds" / "metadata"
SERIELIST = OUT / "serielist"
INDEX = OUT / "index.json"

BASE = "https://evds3.tcmb.gov.tr/igmevdsms-dis"
SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def api_key() -> str:
    """Read EVDS_API_KEY from the environment or .env. Never logged, never committed."""
    import os

    key = os.environ.get("EVDS_API_KEY", "").strip()
    if not key:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("EVDS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or key == "API_KEYINIZ":
        raise SystemExit("EVDS_API_KEY is not set. Put it in .env (which is gitignored).")
    return key


def get(client: httpx.Client, path: str, key: str) -> list | dict:
    r = client.get(f"{BASE}/{path}", headers={"key": key})
    r.raise_for_status()
    return r.json()


def walk(refresh: bool, delay: float) -> dict:
    """Fetch categories, datagroups and every group's series list. Resumable."""
    key = api_key()
    OUT.mkdir(parents=True, exist_ok=True)
    SERIELIST.mkdir(parents=True, exist_ok=True)

    with httpx.Client(verify=SSL_CTX, timeout=60, follow_redirects=True) as c:
        cats_f, dgs_f = OUT / "categories.json", OUT / "datagroups.json"
        if refresh or not cats_f.exists():
            cats_f.write_text(
                json.dumps(get(c, "categories/type=json", key), ensure_ascii=False), "utf-8"
            )
        if refresh or not dgs_f.exists():
            dgs_f.write_text(
                json.dumps(get(c, "datagroups/mode=0&type=json", key), ensure_ascii=False), "utf-8"
            )

        cats = json.loads(cats_f.read_text(encoding="utf-8"))
        dgs = json.loads(dgs_f.read_text(encoding="utf-8"))
        print(f"categories : {len(cats)}")
        print(f"datagroups : {len(dgs)}")

        todo = [
            d for d in dgs if refresh or not (SERIELIST / f"{d['DATAGROUP_CODE']}.json").exists()
        ]
        print(f"serie lists: {len(dgs) - len(todo)} cached, {len(todo)} to fetch")
        if todo:
            print(f"est.       : {len(todo) * (delay + 0.3) / 60:.1f} min")

        failures = 0
        for i, d in enumerate(todo, 1):
            code = d["DATAGROUP_CODE"]
            try:
                items = get(c, f"serieList/type=json&code={code}", key)
                (SERIELIST / f"{code}.json").write_text(
                    json.dumps(items, ensure_ascii=False), "utf-8"
                )
                failures = 0
            except httpx.HTTPError as e:
                failures += 1
                print(f"  [{i}/{len(todo)}] {code}: {type(e).__name__}")
                if failures >= 5:
                    print("Five consecutive failures; stopping rather than hammering EVDS.")
                    break
                time.sleep(delay * 4)
                continue
            if i % 50 == 0 or i == len(todo):
                print(f"  [{i}/{len(todo)}] {code}")
            time.sleep(delay)

    return build_index(cats, dgs)


def build_index(cats: list, dgs: list) -> dict:
    """Flatten every cached series list, attaching the unit from its datagroup.

    A datagroup points at a *leaf* category ("KREDİLER", "ORANLAR"), not at the topic it
    sits under, so the top-level topic has to be resolved by walking UST_CATEGORY_ID up
    to SEVIYE 1. Filtering on the leaf name instead rejects almost everything, because
    there are 124 leaves and none of them are called "PARASAL VE FİNANSAL İSTATİSTİKLER".
    """
    category_name = {int(c["CATEGORY_ID"]): c["TOPIC_TITLE_TR"].strip() for c in cats}
    parent = {int(c["CATEGORY_ID"]): int(c["UST_CATEGORY_ID"]) for c in cats}

    def topic_of(category_id: int) -> str:
        """Climb to the SEVIYE 1 ancestor. Guarded against a cycle in published data."""
        seen: set[int] = set()
        cid = category_id
        while cid in parent and parent[cid] != -1 and cid not in seen:
            seen.add(cid)
            cid = parent[cid]
        return category_name.get(cid, "")

    by_code = {d["DATAGROUP_CODE"]: d for d in dgs}

    series = []
    for f in sorted(SERIELIST.glob("*.json")):
        items = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            continue
        dg = by_code.get(f.stem, {})
        cid = int(dg.get("CATEGORY_ID") or -1)
        for it in items:
            series.append(
                {
                    "code": it.get("SERIE_CODE"),
                    "name": " ".join(str(it.get("SERIE_NAME") or "").split()),
                    "datagroup": f.stem,
                    "datagroup_name": dg.get("DATAGROUP_NAME", ""),
                    "category": category_name.get(cid, ""),
                    "topic": topic_of(cid),
                    # The unit lives here, on the group - not on the series.
                    "unit": (dg.get("BIRIMI") or "").strip(),
                    "frequency": it.get("FREQUENCY_STR"),
                    "default_agg": it.get("DEFAULT_AGG_METHOD"),
                    "sumable": it.get("SUMABLE"),
                    "start": it.get("START_DATE"),
                    "end": it.get("END_DATE"),
                }
            )

    index = {
        "generated_at": datetime.now(UTC).isoformat(),
        "categories": len(cats),
        "datagroups": len(dgs),
        "serie_lists_cached": len(list(SERIELIST.glob("*.json"))),
        "series": series,
    }
    INDEX.write_text(json.dumps(index, ensure_ascii=False), "utf-8")
    return index


def search(index: dict, needle: str, limit: int) -> None:
    """Case-insensitive substring search over series and datagroup names."""
    n = needle.casefold()
    hits = [
        s
        for s in index["series"]
        if n in (s["name"] or "").casefold()
        or n in (s["datagroup_name"] or "").casefold()
        or n in (s["code"] or "").casefold()
    ]
    print(f"\n'{needle}': {len(hits)} of {len(index['series']):,} series")
    for s in hits[:limit]:
        print(f"   {s['code']:<24} {s['name'][:58]:<58} {s['unit'][:18]:<18} {s['frequency']}")
    if len(hits) > limit:
        print(f"   ... and {len(hits) - limit} more")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--search", action="append", default=None, help="substring, repeatable")
    ap.add_argument("--limit", type=int, default=25)
    a = ap.parse_args()

    if INDEX.exists() and not a.refresh and a.search:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    else:
        index = walk(a.refresh, a.delay)

    print(f"\nseries indexed: {len(index['series']):,}")
    with_unit = sum(1 for s in index["series"] if s["unit"])
    print(f"with a unit   : {with_unit:,} ({with_unit / max(len(index['series']), 1):.0%})")

    for needle in a.search or []:
        search(index, needle, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
