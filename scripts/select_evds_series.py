#!/usr/bin/env python3
"""SCRUM-99 - choose the EVDS series to ingest, from the walked catalogue.

    .venv/bin/python scripts/select_evds_series.py --dry-run
    .venv/bin/python scripts/select_evds_series.py --target 250 --write

Reads data/bronze/evds/metadata/index.json (scripts/walk_evds_catalog.py) and writes
config/evds-series.json. The point is that the selection is a filter anyone can re-run
and argue with, not a list someone typed.

Four rules, each of which exists to keep an unusable series out rather than to be tidy:

1. **The category has to be relevant.** EVDS publishes 154 topic categories; a lakehouse
   agent answering questions about Turkish banking needs monetary and financial
   statistics, prices to deflate with, exchange rates to convert YP with, and the real
   sector series that act as denominators. International and balance-of-payments detail
   is not wrong, just not what anything here asks about.

2. **The unit has to be normalisable.** EVDS carries the unit on the datagroup, and 164
   of 678 groups leave it blank while a handful give two possibilities ("Yüzde, TL").
   A series whose unit we cannot state fails is_usable() anyway, so ingesting it spends
   requests to produce a row nothing may serve.

3. **The coverage has to reach our window.** A series that stopped publishing in 2019 or
   started in 2024 cannot answer a 2021-2026 question. Checked against the metadata's
   own START_DATE and END_DATE rather than discovered after ingesting.

4. **Everything already ingested stays.** The first 22 were picked for the demo scenario
   and turn 1 resolves against them. A wider net must not silently drop one.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.catalog.schema import normalise_unit  # noqa: E402

INDEX = ROOT / "data" / "bronze" / "evds" / "metadata" / "index.json"
CONFIG = ROOT / "config" / "evds-series.json"

# EVDS top-level topics worth ingesting, by the prefix of their category id.
TARGET_CATEGORIES = {
    "PARASAL VE FİNANSAL": "krediler, mevduat, faizler - the core of every question here",
    "FİYAT ENDEKSLERİ": "TÜFE/ÜFE, needed to state anything in real terms",
    "DÖVİZ KURLARI": "YP pozisyonlarını TL'ye çevirmek için",
    "BÜYÜME, İSTİHDAM": "GSYH and employment, the usual denominators",
    "REEL SEKTÖR": "konut fiyat endeksi and sector activity",
    "ÖDEME SİSTEMLERİ": "kart harcamaları - closest thing to a spending flow",
    "BEKLENTİ VE EĞİLİM": "banka kredileri eğilim anketi - lending standards",
}

FREQ_MAP = {
    "GÜNLÜK": "daily",
    "İŞ GÜNÜ": "daily",
    "HAFTALIK(CUMA)": "weekly",
    "HAFTALIK(ÇARŞAMBA)": "weekly",
    "HAFTALIK": "weekly",
    "AYLIK": "monthly",
    "ÜÇ AYLIK": "quarterly",
    "YILLIK": "yearly",
    # "AYDA İKİ KEZ" and "ALTI AYLIK" are deliberately absent: the ingestion has no
    # frequency for twice-monthly or half-yearly, and inventing one would resample data
    # into periods it was never published for.
}

# How far short of the window's end a series may stop and still count as covering it.
# A quarterly series whose last point is 2026-Q1 covers a window ending 2026-06-30 as
# fully as quarterly data can; demanding a June stamp would reject every quarterly
# series in the catalogue, including the household loan flow that answers SCRUM-95.
FREQ_GRACE_DAYS = {
    "daily": 31,
    "weekly": 31,
    "monthly": 62,
    "quarterly": 185,
    "yearly": 400,
}

# No single table may crowd out the rest. Without this the highest-scoring 228 all come
# from a handful of large credit datagroups and the working set is 212 loan balances with
# no rate, no price index and no flow to put them against.
PER_DATAGROUP_CAP = 8

# Series required by name, each because something specific needs it.
MUST_INCLUDE: dict[str, str] = {
    # The only loan FLOW published anywhere we hold. Net incurrence of loan liabilities
    # by households per quarter - new lending minus repayments - so turn 1 can answer
    # with a flow instead of only a balance. Settles SCRUM-95 / DECISIONS #10.
    "TP.FINHESTNKS61014.ZP34": "hanehalkı kredi akımı (net), üç aylık",
    "TP.FINHESTNKS61014.ZP36": "hanehalkı uzun vadeli kredi akımı (net) - konut ağırlıklı",
    "TP.FINHESTNKS61014.ZP35": "hanehalkı kısa vadeli kredi akımı (net)",
}

# Units for already-ingested series whose own datagroup publishes no BIRIMI. Each is
# taken from EVDS metadata elsewhere, not from the shape of the numbers:
#
#   TP.AB.B6        its datagroup does publish one; it was only excluded from the walk's
#                   eligible set because the topic is out of scope.
#   TP.DK.*.S       the archived Kurlar group is silent, but the live one (bie_dkdovytl,
#                   the same quantity) publishes "Türk lirası".
#
# TP.KKM.K4 is deliberately absent. bie_kkm publishes no BIRIMI and its note does not
# say. The magnitude is consistent with milyar TL, but consistent is not stated, and a
# unit guessed from magnitude is exactly what this catalog refuses to serve.
CARRIED_OVER_UNIT: dict[str, str] = {
    "TP.AB.B6": "milyon ABD doları",
    "TP.DK.USD.S": "Türk lirası",
    "TP.DK.EUR.S": "Türk lirası",
}

# Prefer series a banking question actually reaches for, when trimming to the target.
PRIORITY_TERMS = (
    "kredi",
    "mevduat",
    "faiz",
    "takip",
    "tüketici",
    "konut",
    "taşıt",
    "ihtiyaç",
    "ticari",
    "kart",
    "tüfe",
    "üfe",
    "kur",
    "rezerv",
    "katılım",
    # The financial-accounts flow lines. These are the only loan *flow* published
    # anywhere we hold, so they outrank another balance (SCRUM-95, DECISIONS #10).
    "konsolide akım",
    "hanehalkı",
)


def parse_evds_date(text: str | None) -> date | None:
    """EVDS writes dates as DD-MM-YYYY."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d-%m-%Y").date()
    except ValueError:
        return None


def category_reason(topic: str) -> str | None:
    """Why this series' top-level topic is in scope, or None to reject it.

    EVDS keeps 22,010 of its 53,792 series under ARŞİV - superseded publications it
    still serves. Those are excluded by never matching a target prefix.
    """
    for prefix, why in TARGET_CATEGORIES.items():
        if topic.upper().startswith(prefix):
            return why
    return None


def resolve_unit(series: dict) -> tuple[str, str]:
    """The series' unit and where it came from.

    Normally the datagroup's BIRIMI. 164 of 678 groups leave it blank, and for one
    recognisable case the series name settles it anyway: a "... Endeksi" is an index
    whatever the group forgot to say. Everything else stays blank and gets rejected,
    because a unit guessed from prose is how a plausible wrong number reaches a chart.

    The source is returned so the config records which series were resolved this way.
    """
    published = (series.get("unit") or "").strip()
    if published:
        return published, "datagroup"
    if "endeks" in (series.get("name") or "").casefold():
        return "Endeks", "series name"
    return "", ""


def score(series: dict) -> int:
    """How strongly a banking question would reach for this series."""
    text = f"{series['name']} {series['datagroup_name']}".casefold()
    return sum(1 for term in PRIORITY_TERMS if term in text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=250)
    ap.add_argument("--write", action="store_true", help="write config/evds-series.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not INDEX.exists():
        print("Run scripts/walk_evds_catalog.py first.")
        return 1

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    existing = json.loads(CONFIG.read_text(encoding="utf-8"))
    keep_codes = {s["code"] for s in existing["series"]}
    window_start = date.fromisoformat(existing["start_date"])
    window_end = date.fromisoformat(existing["end_date"])

    rejected = {
        "category": 0,
        "archived": 0,
        "unit": 0,
        "coverage": 0,
        "frequency": 0,
        "duplicate": 0,
    }
    candidates: list[dict] = []
    seen: set[str] = set()

    for s in index["series"]:
        code = (s.get("code") or "").strip()
        if not code or code in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(code)

        if category_reason(s.get("topic") or "") is None:
            rejected["category"] += 1
            continue
        if "(ARŞİV)" in (s.get("name") or "").upper():
            rejected["archived"] += 1
            continue
        unit, unit_source = resolve_unit(s)
        unit_norm, _ = normalise_unit(unit)
        if not unit_norm:
            rejected["unit"] += 1
            continue
        freq = FREQ_MAP.get((s.get("frequency") or "").strip().upper())
        if freq is None:
            rejected["frequency"] += 1
            continue
        start, end = parse_evds_date(s.get("start")), parse_evds_date(s.get("end"))
        grace = timedelta(days=FREQ_GRACE_DAYS[freq])
        if start is None or end is None or start > window_start or end < window_end - grace:
            rejected["coverage"] += 1
            continue

        candidates.append(
            {
                "code": code,
                "name": s["name"],
                "frequency": freq,
                "unit": unit,
                "unit_source": unit_source,
                "datagroup": s.get("datagroup", ""),
                "category": s.get("topic", "") or s.get("category", ""),
                "_score": score(s),
            }
        )

    print(f"indexed      : {len(index['series']):,} series")
    for reason, n in rejected.items():
        print(f"  rejected {reason:<9}: {n:,}")
    print(f"eligible     : {len(candidates):,}")

    by_code = {c["code"]: c for c in candidates}
    must_keep = [by_code[c] for c in keep_codes if c in by_code]
    missing = sorted(keep_codes - set(by_code))

    pinned = [c for c in candidates if c["code"] in MUST_INCLUDE and c["code"] not in keep_codes]
    for c in pinned:
        c["note"] = MUST_INCLUDE[c["code"]]
    pinned_codes = {c["code"] for c in pinned}
    missing_pins = sorted(set(MUST_INCLUDE) - pinned_codes - keep_codes)

    # Fill the remainder highest-score-first, but never more than the cap from one table.
    per_group: dict[str, int] = {}
    for c in must_keep + pinned:
        per_group[c["datagroup"]] = per_group.get(c["datagroup"], 0) + 1

    rest: list[dict] = []
    room = max(a.target - len(keep_codes) - len(pinned), 0)
    for c in sorted(candidates, key=lambda c: (-c["_score"], c["code"])):
        if len(rest) >= room:
            break
        if c["code"] in keep_codes or c["code"] in pinned_codes:
            continue
        used = per_group.get(c["datagroup"], 0)
        if used >= PER_DATAGROUP_CAP:
            continue
        per_group[c["datagroup"]] = used + 1
        rest.append(c)

    chosen = must_keep + pinned + rest
    for c in chosen:
        c.pop("_score", None)
    chosen.sort(key=lambda c: c["code"])

    print(f"\nalready ingested kept : {len(must_keep)} of {len(keep_codes)}")
    if missing:
        print(f"  NOT in the walk     : {', '.join(missing)}")
        print("  (kept anyway - they are ingested and verified)")
    print(f"pinned by name        : {len(pinned)}")
    for c in pinned:
        print(f"     {c['code']:<26} {c['note']}")
    if missing_pins:
        print(f"  MISSING PINS        : {', '.join(missing_pins)}  <- investigate")
    print(f"filled by score       : {len(rest)} (max {PER_DATAGROUP_CAP} per datagroup)")
    print(f"total                 : {len(chosen)}")
    print(f"distinct datagroups   : {len({c['datagroup'] for c in chosen})}")

    from collections import Counter

    print("\nby category")
    for cat, n in Counter(c["category"] for c in chosen).most_common():
        print(f"   {n:>4}  {cat[:66]}")
    print("\nby unit")
    for unit, n in Counter(c["unit"] for c in chosen).most_common(10):
        print(f"   {n:>4}  {unit}")

    # Anything kept but absent from the walk still needs an entry, taken from the old config.
    old_by_code = {s["code"]: s for s in existing["series"]}
    for code in missing:
        entry = dict(old_by_code[code])
        if not entry.get("unit"):
            unit = CARRIED_OVER_UNIT.get(code, "")
            entry["unit"] = unit
            entry["unit_source"] = "EVDS metadata, sibling datagroup" if unit else ""
        chosen.append(entry)
    chosen.sort(key=lambda c: c["code"])

    payload = {
        "start_date": existing["start_date"],
        "end_date": existing["end_date"],
        "is_full_evds_catalog": False,
        "scope_note": (
            "EVDS kataloğunun tamamı yürünerek (154 kategori, 678 veri grubu) "
            f"{len(index['series']):,} seri indekslendi; bunlardan bankacılık sorularına "
            "cevap veren, birimi bilinen ve 2021-2026 aralığını kapsayan seriler seçildi. "
            "Seçim scripts/select_evds_series.py ile yeniden üretilebilir."
        ),
        "known_gaps": [],  # filled below
        "series": chosen,
    }

    gaps = [
        "EVDS veri gruplarının 164'ünde birim (BIRIMI) alanı boş; birimi belirtilemeyen "
        "seri servis edilemediği için bu gruplar seçime dahil edilmedi.",
        "Uluslararası istatistikler, ödemeler dengesi ve TCMB bilanço kategorileri "
        "kapsam dışı bırakıldı; bu projenin sorduğu sorular bunlara değmiyor.",
    ]
    payload["known_gaps"] = gaps

    if a.dry_run or not a.write:
        print("\n--dry-run: config not written. Pass --write to apply.")
        return 0

    CONFIG.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"\nwrote {CONFIG.relative_to(ROOT)} with {len(chosen)} series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
