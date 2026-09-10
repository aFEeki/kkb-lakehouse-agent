# KKB Lakehouse Agent

Agentic analytics over Turkish banking and macroeconomic data. Ask a question in Turkish;
the system plans the analysis, selects its own tools, computes the answer, verifies it,
and shows the provenance of every number it reports.

**KKB Hackathon 2026** — Lakehouse Agent Builder & Data Analytics
**Team:** Fellas in Istanbul

> **Özet.** BDDK ve EVDS verileri üzerinde çalışan uçtan uca agentic analiz sistemi.
> Türkçe sorulan bir soruyu anlar, analizi planlar, uygun araçları seçer, çok adımlı
> hesaplamayı yürütür, sonucu doğrular ve karar destek çıktısına dönüştürür. Ürettiği her
> sayı, kaynak seriye ve uygulanan dönüşüm zincirine kadar izlenebilir.

---

## What it does

The system answers analytical questions over a prepared data pool built from BDDK bulletins
and TCMB EVDS, January 2021 to June 2026, and enriches them with live sources on request.

A worked example — the same conversation the organizers published, which the system treats
as its primary acceptance test:

| Turn | Question | What the system does |
|---|---|---|
| 1 | Monthly housing loan volume 2021–2025 with interest rates. Did volume rise when rates fell? | Resolves two series, aligns them onto a shared monthly spine, charts them, and answers from computed evidence |
| 2 | *Without breaking the table*, deflate only the loan amounts for inflation | Brings in a price index, applies it to one column, leaves rows and other columns untouched |
| 3 | *Without breaking this table at all*, add the house price index. Could prices explain the weak loan growth? | Joins a fourth series onto the same rows, then revises its own earlier conclusion |

The system carries one live analysis object across a conversation and modifies it in place.
It does not rebuild the result from scratch on each turn — see [Design decisions](#design-decisions).

---

## Architecture

Five stages, as specified in the brief, with a trust layer running underneath all of them.

| Stage | Implementation | Module |
|---|---|---|
| **1 · Data Discovery & Acquisition** | Crawl and archive BDDK bulletins and EVDS series. Raw bytes preserved with SHA-256 and retrieval timestamp before anything is parsed | `ingest/`, `data/bronze/` |
| **2 · Data Cleaning & Alignment** | Quality and gap checks, de-cumulation, unit normalisation, frequency harmonisation. Output is one pool where any series joins to any other | `transform/`, `catalog/`, `data/gold/` |
| **3 · Agentic Analytics Engine** | Turkish question understanding, series resolution over hybrid semantic and lexical retrieval, tool selection, multi-step execution against the analysis object | `agent/`, `tools/lakehouse.py` |
| **4 · Analysis** | Anomaly detection, change-point detection, and causality testing with break-aware specification | `tools/anomaly.py`, `tools/change_detection.py`, `tools/causality.py` |
| **5 · Verification & Output** | Corroboration through web search and direct URL reading; output rendered as chart, table or written report | `tools/web_search.py`, `tools/web_url.py`, `api/` |

### Trust layer

Traceability is a property of the data structure, not a footnote appended to the answer.
Every column in every result carries a `Lineage` record: its source kind, an exact source
reference (an EVDS series code, or a workbook, sheet and cell range), the retrieval
timestamp, the SHA-256 of the raw payload it was parsed from, and the ordered chain of
transformations applied to it. Derived columns carry their parents' lineage recursively.

The interface exposes this per column, so "where does this number come from" is answered by
clicking it rather than by consulting a list of sources at the bottom of the page.

Accuracy control is enforced by an invariant suite that gates the data build. Failures block
the build; they are not warnings. The invariants are documented in [`docs/invariants.md`](docs/).

---

## Agent tools

| Tool | Capability |
|---|---|
| **Lakehouse** | Natural-language discovery, query and join over the prepared pool |
| **Web Search** | Research and corroboration against external sources |
| **Web URL Agent** | Reads an arbitrary URL and extracts meaning — PDF, Excel, image and text, including documents linked from the page rather than at it |
| **Anomaly** | Unusual movements, outliers and deviations from expected behaviour |
| **Causality** | Whether an observed relationship is genuinely causal, or only correlated |
| **Change Detection** | Level, trend and behavioural breaks in a time series |

The planner routes each question to the tools it needs and cites them in the answer. Tool
selection is visible in the execution trace rather than hidden.

---

## Design decisions

Two choices shape everything else, and both are deliberate.

### The analysis object has an immutable spine

The result of a question is a persistent, versioned object — a fixed date spine plus typed
columns, each with its own lineage, plus findings and a chart specification. The planner
emits operations against that object from a closed vocabulary; it never regenerates the
object and never performs arithmetic itself.

Every column-adding operation left-joins onto the existing spine and asserts that row
cardinality is unchanged. Re-slicing the spine is a separate operation requiring explicit
user confirmation. This is what makes "without breaking the table" a guarantee the system
enforces rather than a behaviour it hopes for: a planner that decides to inner-join or
re-window cannot do so silently.

The same op log gives the system a working undo, so a mis-specified turn can be reverted
without discarding earlier ones.

### Cumulative data is classified per series, and verified by a person

BDDK publishes some figures as running totals — some resetting each January, others never
resetting — alongside ordinary period values. The three are visually indistinguishable on a
chart, and misclassifying one corrupts every period-over-period figure derived from it.

The obvious detector fails here. "Monotonically increasing implies cumulative" is close to
worthless for Turkish lira series over this period, because nominal values rose in almost
every month regardless of what they measured. What discriminates is the January
discontinuity, so classification is built around that test, recorded with its supporting
evidence in the catalog, and confirmed by a person before the series is eligible for the
gold layer. A series that cannot be verified is dropped rather than published.

Each series also carries an explicit aggregation rule, because de-cumulation and frequency
conversion are coupled: a de-cumulated weekly flow sums to a month, while a stock takes the
end-of-period value.

---

## Data sources

| Source | Coverage | Notes |
|---|---|---|
| **BDDK** — Haftalık Bülten, Aylık Bülten, FinTürk | 2021-01 → 2026-06 | Excel bulletins; multi-row headers, mixed units, cumulative and non-cumulative series |
| **TCMB EVDS** | 2021-01 → 2026-06 | Daily, weekly and monthly series via the documented API |
| **Live URLs** | On demand | Supplied at query time and read directly |

Series are pinned to a dated snapshot so results are reproducible; the snapshot date is
shown in the interface, since both sources revise history.

---

## Technology

Python throughout the backend and agent layer, per the competition rules. Open-source
libraries only.

| Concern | Choice |
|---|---|
| Inference | Kloudeks MIA — `Qwen3.8-27B` (reasoning, vision), `Qwen3-Embedding-8B` (retrieval), `Unlimited-OCR` (documents) |
| Analytical store | DuckDB |
| Vector store | LanceDB |
| Dataframes | Pandas |
| Documents | pypdf, pypdfium2, openpyxl |
| Statistics | statsmodels, ruptures, SciPy |
| Charts | Plotly |
| API | FastAPI |
| Interface | Next.js |

No third-party LLM API service is used at runtime. All inference goes through the Kloudeks
platform, and the credential is held server-side only — the frontend calls our API, never
the model endpoint.

---

## Repository layout

```
src/kkb_agent/
  llm/          Kloudeks MIA client — the only inference provider
  frame/        The analysis object: spine, columns, lineage, operations
  ingest/       BDDK and EVDS acquisition into the bronze layer
  catalog/      Series metadata — units, cumulative mode, aggregation rule, semantics
  transform/    De-cumulation, unit normalisation, frequency harmonisation
  tools/        The six agent tools
  agent/        Planner and execution loop
  api/          FastAPI backend

web/            Next.js interface
tests/          Invariant suite gating the data build
scripts/        Ingest jobs and platform capability probes
eval/           Frozen Turkish question set used for regression
docs/           Architecture, database definitions, capability sheet
data/           Local lake (gitignored) — bronze → silver → gold
```

---

## Running it

```bash
cp .env.example .env          # MIA_API_KEY, EVDS_API_KEY
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

python scripts/build_lakehouse.py    # acquire → parse → harmonise → verify
pytest -m invariant                  # data-correctness gate
uvicorn kkb_agent.api.main:app --reload
```

A live deployment is available at *(URL to be added)*.

---

## Status

Under active development for the 20 September submission. See [PLAN.md](PLAN.md) for the
build sequence. This section is updated as components land.

---

## Team

Fellas in Istanbul — *(members to be listed)*

## Contributing

Local settings, secrets and the data lake are gitignored; check `git status` before
committing. The MIA credential is environment-only and must never appear in frontend code,
a commit, or a screen capture.
