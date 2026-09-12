#!/usr/bin/env python3
"""Ingest the committed curated EVDS list into the local silver layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kkb_agent.config import Settings
from kkb_agent.ingest.evds import EVDSClient
from kkb_agent.ingest.evds_ingestion import CuratedEVDSIngestor, load_curated_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "evds-series.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    settings = Settings()
    config = load_curated_config(args.config)
    output = args.output or settings.data_dir / "silver" / "evds"
    with EVDSClient(settings) as client:
        report = CuratedEVDSIngestor(client, output).ingest(config)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
