# KKB Lakehouse Agent

**Team:** Fellas in Istanbul · **Event:** KKB Hackathon 2026 — Lakehouse Agent Builder & Data Analytics

Agentic analytics over BDDK and EVDS data. Ask a question in Turkish, get an answer with
real numbers, a chart, and a traceable source for every figure.

See [PLAN.md](PLAN.md) for what we're building and the day-by-day plan.

## Layout

```
src/kkb_agent/
  llm/          MIA (Kloudeks) client. The ONLY LLM provider — no other may be added.
  frame/        The analysis object: spine, columns, lineage, operations. The keystone.
  ingest/       Acquisition from BDDK and EVDS. Raw bytes + hashes into data/bronze.
  catalog/      Series metadata: units, cumulative mode, aggregation rule, semantics.
  transform/    De-cumulation, unit normalisation, frequency harmonisation.
  tools/        The six required tools (lakehouse, search, url, anomaly, causality, change).
  agent/        Planner and executor. Routes a question to series + tools + operations.
  api/          FastAPI backend. Holds the API key — the frontend never does.

web/            Next.js frontend (D8). Talks to api/, never to MIA directly.
tests/          Invariant suite. Blocks the gold build when the numbers don't reconcile.
scripts/        One-off runners: ingest jobs, the MIA capability probe.
eval/           Frozen question set for regression (~25 Turkish questions).
docs/           Architecture, DB definitions, MIA capability sheet. Deliverable to KKB.
data/           Local lake — gitignored, rebuilt by the ingest scripts.
  bronze/       Raw source files, untouched, with hashes.
  silver/       Parsed and tidied, still in source units.
  gold/         Harmonised, de-cumulated, joinable. DuckDB + LanceDB live here.
```

## Setup

```bash
cp .env.example .env        # fill in MIA_API_KEY and EVDS_API_KEY
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## Repo rules

Non-negotiable — the repo goes to KKB and the history is part of the submission.

1. **Never commit** `.claude/`, `.env`, `CLAUDE.local.md`, or local settings. Already in
   `.gitignore`; check `git status` before every commit.
2. **Never commit the MIA API key.** Environment variable only. Never in frontend code,
   never in a screenshot or a slide. If one leaks, rotate it with the organizers.
3. **No assistant attribution** in commits, PR titles, or descriptions. The history reads
   as ours.
