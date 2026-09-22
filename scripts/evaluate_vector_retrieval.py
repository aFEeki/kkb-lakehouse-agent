#!/usr/bin/env python3
"""Read-only live catalog evaluation. No index/golden/data writes."""

import json
from dataclasses import asdict

from kkb_agent.catalog.hybrid import HybridRetrieval
from kkb_agent.catalog.retrieval import build_concepts, resolve
from kkb_agent.catalog.vector_index import SemanticIndex, catalog_rows
from kkb_agent.config import Settings
from kkb_agent.llm.client import MIAClient
from kkb_agent.llm.embeddings import MIAEmbeddingProvider

BALANCE = "bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut"
CASES = (
    ("konut kredisi bakiyesi", BALANCE),
    ("konut kredisi faiz oranı", "evds.TP.KTF12"),
    ("tüketici fiyat endeksi", "evds.TP.GENENDEKS.T1"),
    ("konut fiyat endeksi", "evds.TP.KFE.TR"),
    ("Ev satın almak için bankalara kalan borç bakiyesi", BALANCE),
    ("Ev satın alma kredisinin faiz maliyeti", "evds.TP.KTF12"),
    ("Hanehalkının alışveriş sepeti fiyat endeksi", "evds.TP.GENENDEKS.T1"),
    ("Türkiye'de evlerin satış fiyatlarını izleyen endeks", "evds.TP.KFE.TR"),
)


class RecordedIndex(SemanticIndex):
    last = None

    def search(self, *args, **kwargs):
        self.last = super().search(*args, **kwargs)
        return self.last


def main() -> int:
    settings = Settings()
    rows = catalog_rows(settings.duckdb_path)
    client = MIAClient(settings)
    index = RecordedIndex(settings.lancedb_path, MIAEmbeddingProvider(client))
    counts = dict.fromkeys(("EXACT_CORRECT", "SAFE_AMBIGUITY", "WRONG_SELECTION", "NO_RESULT"), 0)
    policy_pass = True
    try:
        for case_number, (query, expected) in enumerate(CASES, 1):
            index.last = None
            lexical = HybridRetrieval().resolve(query, rows)
            lexical_hits = resolve(build_concepts(rows), query, limit=5).hits
            lexical_candidates = [
                r["series_id"]
                for hit in lexical_hits
                for r in rows
                if (r["source"], r["raw_label"]) == hit.concept.key
            ][:5]
            result = HybridRetrieval(index, top_k=settings.vector_top_k, mode="selective").resolve(
                query, rows
            )
            if result.resolution.series_ids == (expected,):
                classification = "EXACT_CORRECT"
            elif result.ambiguity:
                classification = "SAFE_AMBIGUITY"
            elif result.resolution.series_ids:
                classification = "WRONG_SELECTION"
            else:
                classification = "NO_RESULT"
            counts[classification] += 1
            accepted = classification == "EXACT_CORRECT" or (
                classification == "SAFE_AMBIGUITY" and case_number in (3, 5, 7)
            )
            policy_pass = policy_pass and accepted
            print(
                json.dumps(
                    {
                        "query": query,
                        "expected": expected,
                        "lexical": lexical.resolution.series_ids,
                        "lexical_top5": lexical_candidates,
                        "vector_top5": [c.series_id for c in index.last.candidates[:5]]
                        if index.last
                        else [],
                        "hybrid": result.resolution.series_ids,
                        "classification": classification,
                        "ambiguity": asdict(result.ambiguity) if result.ambiguity else None,
                        "trace": asdict(result.trace),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        print(json.dumps({"totals": counts, "catalog_rows": len(rows)}))
        # Ambiguity is safe but does not fulfill a clearly resolvable exact-ID case.
        return 0 if policy_pass else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
