#!/usr/bin/env python3
"""SCRUM-38 / SCRUM-39 - try a Turkish question against the catalog.

    .venv/bin/python scripts/resolve_series.py "konut kredisi faizi"
    .venv/bin/python scripts/resolve_series.py "şube sayısı" --limit 5 --series
    .venv/bin/python scripts/resolve_series.py --selftest

Resolution is two-stage. A question names a MEASURE, which is matched against the 872
distinct measures in the catalog; and it names FACETS - a province, a bank group, a
frequency - which filter the concrete series behind that measure rather than being scored.

`--series` shows the second stage: which concrete series_ids the chosen measure resolves
to, and what facets are available to narrow it further.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kkb_agent.catalog.retrieval import build_concepts, resolve  # noqa: E402

GOLD = ROOT / "data" / "gold" / "lakehouse.duckdb"

CATALOG_COLUMNS = (
    "source, raw_label, name_tr, measure_type, unit_normalized, sector_scope, native_freq, province"
)

# Questions whose right answer we know, so a regression in scoring is visible rather than
# something noticed later in a demo. Each is (query, expected label, expected measure).
SELFTEST = [
    ("konut kredisi faizi", "Konut Kredisi (TL, Akım, %)", "rate"),
    ("konut kredisi", "Konut Kredisi", "stock"),
    ("tüketici fiyat endeksi", "Tüketici Fiyat Endeksi (Genel)", "index"),
    ("konut fiyat endeksi", "Konut Fiyat Endeksi (KFE)", "index"),
    ("şube sayısı", "Yurtiçi Şube Sayısı", "count"),
    ("banka sayısı", "Banka Sayısı", "count"),
    ("likidite yeterlilik oranı", "Likidite Yeterlilik Oranı", "ratio"),
    ("toplam aktifler", "TOPLAM AKTİFLER", "stock"),
]


def load_concepts(con: duckdb.DuckDBPyConnection):
    rows = con.execute(f"SELECT {CATALOG_COLUMNS} FROM series_catalog").fetchdf()
    return build_concepts(rows.to_dict("records"))


def show_series(con: duckdb.DuckDBPyConnection, source: str, label: str, limit: int) -> None:
    """The second stage: concrete series behind one measure, and the facets to pick among."""
    facets = con.execute(
        """SELECT count(*) n,
                  count(DISTINCT sector_scope) scopes,
                  count(DISTINCT province)     provinces,
                  count(DISTINCT native_freq)  freqs
           FROM series_catalog WHERE source = ? AND raw_label = ?""",
        [source, label],
    ).fetchone()
    print(
        f"      {facets[0]} series · {facets[1]} bank-group scope(s) · "
        f"{facets[2]} province(s) · {facets[3]} frequency/ies"
    )
    for r in con.execute(
        """SELECT series_id, sector_scope, province, native_freq, unit_raw, observations
           FROM series_catalog WHERE source = ? AND raw_label = ?
           ORDER BY province NULLS FIRST, sector_scope LIMIT ?""",
        [source, label, limit],
    ).fetchall():
        where = r[2] or "Türkiye"
        print(f"        {r[0][:52]:<52} {r[1][:18]:<18} {where[:12]:<12} {r[3]} {r[4]} n={r[5]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="*", help="the Turkish question")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--series", action="store_true", help="expand the top hit into series")
    ap.add_argument("--selftest", action="store_true", help="check the known-answer set")
    ap.add_argument("--db", type=Path, default=GOLD)
    a = ap.parse_args()

    if not a.db.exists():
        print(f"{a.db} not found. Run scripts/build_catalog.py first.")
        return 1

    con = duckdb.connect(str(a.db), read_only=True)
    concepts = load_concepts(con)
    total = con.execute("SELECT count(*) FROM series_catalog").fetchone()[0]
    print(f"{total:,} series -> {len(concepts)} distinct measures\n")

    if a.selftest:
        failures = 0
        for query, expected_label, expected_measure in SELFTEST:
            r = resolve(concepts, query, limit=1)
            got = r.best.concept.label if r.best else "— no match"
            measures = sorted(r.best.concept.measure_types) if r.best else []
            ok = got == expected_label and expected_measure in measures
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'}  {query:<28} {got[:46]}")
            if not ok:
                print(f"        expected {expected_label!r} ({expected_measure})")
        print(f"\n{len(SELFTEST) - failures}/{len(SELFTEST)} resolved as expected")
        return 1 if failures else 0

    if not a.query:
        ap.error("give a query, or --selftest")

    query = " ".join(a.query)
    r = resolve(concepts, query, limit=a.limit)

    print(f"query  : {query}")
    print(f"intent : {sorted(r.intent.measure_types) or '—'}  {list(r.intent.cues) or ''}")
    if not r.intent_satisfied and r.intent:
        print(
            "         ⚠ nothing in the catalog satisfies that intent. Falling back to the\n"
            "           closest measure of another kind - say so in the answer."
        )
    print()

    if not r.hits:
        print("no match. The catalog holds nothing close enough to serve.")
        return 0

    for i, h in enumerate(r.hits, 1):
        c = h.concept
        print(f"  {i}. {h.score:.3f}  [{c.source}]  {c.label}")
        print(
            f"      {'/'.join(sorted(c.measure_types))} · {'/'.join(sorted(c.units))} · "
            f"{c.series_count} series · matched {', '.join(h.matched)}"
        )
        if a.series and i == 1:
            show_series(con, c.source, c.label, a.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
