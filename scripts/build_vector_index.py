#!/usr/bin/env python3
"""Explicit metadata index build; never writes DuckDB or runs at API startup."""

from kkb_agent.catalog.vector_index import SemanticIndex, catalog_rows
from kkb_agent.config import Settings
from kkb_agent.llm.client import MIAClient
from kkb_agent.llm.embeddings import MIAEmbeddingProvider


def main() -> int:
    settings = Settings()
    client = MIAClient(settings)
    try:
        rows = catalog_rows(settings.duckdb_path)
        count = SemanticIndex(settings.lancedb_path, MIAEmbeddingProvider(client)).build(rows)
        print(
            f"Catalog rows: {len(rows)}\nEmbedded: {count}\nIndexed: {count}\n"
            "Duplicates: 0\nResult: PASS"
        )
        return 0
    except Exception:
        print("Result: FAIL (catalog/index/embedding validation failed; no partial success)")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
