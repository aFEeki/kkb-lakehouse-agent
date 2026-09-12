# Curated EVDS ingestion

`config/evds-series.json` is the committed first-pass EVDS scope. It records the requested
snapshot dates and explicitly states that the list is a development subset rather than the
full EVDS catalog.

Run the ingestion once for the snapshot:

```bash
python scripts/ingest_evds_curated.py
```

The command writes one Parquet file per configured series under `data/silver/evds/`. Each row
contains the series code, observation date, exact decimal value and UTC retrieval timestamp.
An empty response still creates an empty Parquet file and is marked `no_data` in
`data/silver/evds/coverage.json`; it is never silently skipped.

The coverage report records the observed start/end dates per series, missing-value counts,
the requested snapshot range and the known scope gaps. Daily series are requested in bounded
date windows so EVDS's documented 1,000-observation truncation cannot silently remove the
start of the snapshot. Other configured frequencies fit inside that limit for 2021-01 through
2026-06 and use one request per series.

Generated silver data and coverage output remain local and gitignored. The committed config
is the reproducible declaration of scope. This task does not populate the series catalog,
harmonise frequencies or build the gold layer.
