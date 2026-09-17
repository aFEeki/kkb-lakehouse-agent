# Architecture

Companion to `docs/architecture.excalidraw` (open at <https://excalidraw.com> — File → Open,
or the Excalidraw VS Code extension).

The system answers Turkish natural-language questions over BDDK and TCMB EVDS data. Its
organising principle is a division of labour between the model and the code:

| | decides |
|---|---|
| **Model** | *what* to do — which series, which operations, in what order |
| **Code** | *whether* that is legal — schema, vocabulary, frame invariants |
| **Code** | *the arithmetic* — every number in an answer is computed, never generated |

A model that emits a nonsense operation fails validation rather than producing a wrong
number. This is why the answer can be trusted and why `planned_by` is reported alongside
every result: "the agent did this" and "a script did this" must never look the same.

---

## Stage 1 — Veri Keşfi & Temini

Four sources, acquired and stored exactly as received.

| Source | Series | Frequency | Shape |
|---|---:|---|---|
| BDDK Aylık Bülten | 4,960 | monthly | 17 tables × 10 bank-group scopes |
| BDDK Haftalık Bülten | 283 | weekly | HTML |
| BDDK FinTürk — İllere Göre | 41,522 | quarterly | 7 tables × 82 provinces |
| TCMB EVDS | 250 | mixed | API → parquet |

**bronze** holds the responses byte-for-byte, with a JSONL manifest recording each file's
`sha256` and fetch time. Nothing is parsed on the way in — a parser bug must never be able
to destroy the evidence it was run against.

## Stage 2 — Veri Temizliği & Hizalama

`scripts/build_catalog.py` derives **gold** from bronze. It makes no network calls, so the
same bronze always produces the same catalog — the property that lets the snapshot be
shared and every teammate arrive at identical numbers.

Four problems are solved here, each of which produces a *plausible* wrong number if
ignored:

- **Units.** `t05` is in *bin TL* where `t01`–`t04` are *milyon TL*. Every series records
  `unit_raw`, `unit_normalized` and `scale_factor`; a unit that cannot be resolved leaves
  the series unservable rather than guessed.
- **Accumulation.** 599 series are published year-to-date. They are de-cumulated on the way
  in, with the published figure kept alongside in `value_reported` so the transform stays
  reversible and checkable.
- **Identity.** BDDK row labels embed references to *other row numbers*, which shift when a
  row is inserted above. Identity is a composite of source, table, scope and normalised
  label — never the raw label, never row order.
- **Scope.** The same table exists for ten bank groups. Keying without the scope lets them
  overwrite each other; this is a bug we shipped once and now have a standing check for.

### The gate

The build runs seven invariants afterwards and exits non-zero on breach. A failed build
still writes its catalog — seeing the broken numbers is how you diagnose them — but nothing
downstream treats it as good, and the previous snapshot keeps serving.

| | Proves | Coverage |
|---|---|---|
| **I1** | de-cumulated months sum back to the published December figure | 2,875 series-years |
| **I2** | a `count` is never negative *(deliberately narrow — see below)* | 618 series |
| **I3** | year-to-date series show the January reset; pattern/accounting disagreements reported | 4,960 series |
| **I4** | FinTürk table 6 reconstructs table 1, proving its per-capita columns are TL not Bin TL | 1,782 province-quarters |
| **I6** | BDDK's ten scopes form three partitions that each sum to their parent | 89,619 comparisons |
| **I7** | NULL is never interpolated or forward-filled without a recorded operation | every column |
| **I8** | `coverage_end` is consistent with the source's publication lag | every series |

Two properties make these more than decoration. Each compares the data against *its own
publisher's arithmetic* rather than against our expectations — I6 needs no second source at
all. And each **fails when it compares nothing**: a scope-collapsing bug leaves every
partition with a missing child, and "OK, 0 comparisons" would otherwise read as a pass.

I2 is narrow on purpose. Measured against the catalog, negatives are overwhelmingly
legitimate — 161 flow series are net figures, 29 stock series are FX net positions and
retained losses, and de-cumulation legitimately produces them in 91 of 599 year-to-date
series when a provision is released. A universal sign rule would fire on correct data and
be switched off within a day.

All seven run in CI on every push against a committed 1.8 MB slice of real bronze, so a
change that breaks de-cumulation goes red rather than being found on demo day.

Full column-level detail: **`docs/database-definitions.md`**.

## Stage 3 — Agentic Analytics Motoru

**Retrieval.** 47,015 series collapse to **872 distinct measures**. Province, bank group and
currency are structured *facets*: they filter, and are never allowed to influence the
relevance score — otherwise "İstanbul konut kredisi" scores every İstanbul series above the
right measure. Matching is Turkish-aware throughout (`İ`/`I` casefolding, shared-prefix
handling for agglutination), and the resolution records every choice and substitution it
made so the answer can disclose them.

**Planner** (`agent/planner.py`) asks MIA for an operation plan under a JSON schema narrowed
to the identifiers that actually exist in this frame — one schema branch per operation kind,
so a malformed plan is unrepresentable rather than merely rejected. Three attempts, then a
scripted fallback, so the demo stays runnable if MIA is unreachable.

**Executor** (`agent/executor.py`) validates each operation against the frame contracts
before applying it, and performs the arithmetic itself.

### The analysis frame

The versioned object an answer is assembled in. Its contracts are what make "don't break my
table" enforceable rather than aspirational:

- The **spine** is immutable. Adding a shorter series must never truncate it; a computation
  that needs complete rows narrows its own window and says so.
- Every column carries **lineage** — either source references (which series, which
  acquisition, which bytes) or parent columns plus the transformation applied.
- A **finding** cannot be constructed without at least one supporting column. Revising one
  supersedes it rather than deleting it, so the earlier claim stays visible.

## Stage 4 — Verinin Analiz Edilmesi

Six tools, matching the brief:

| Tool | Module | What it does | Status |
|---|---|---|---|
| Lakehouse | `tools/lakehouse.py` | natural-language query and join over the gold DuckDB | built |
| Web URL Agent | `tools/url_agent/` | content-type router, PDF text with OCR fallback, linked-document discovery, JS rendering, per-turn timeouts | built |
| Anomali | `tools/anomaly.py` | outliers and departures from expected behaviour | built |
| Causality | `tools/causality/` | Toda-Yamamoto, VAR/VECM, cointegration, stationarity, breakpoints, regimes, robustness — **with an explicit refusal path** | built |
| Change Detection | `tools/change_detection.py` | level, trend and behaviour breaks in a series | built |
| Web Search | — | enrichment from the open web | **not yet implemented** |

The causality tool refusing is a feature, not a gap. Most pairs of Turkish macro series
over 2021–2025 are trending together because inflation peaked near 85%; a tool that always
returns a causal verdict would be confidently wrong most of the time.

## Stage 5 — Doğrulama & Sonuç

`POST /ask` streams Server-Sent Events, one per pipeline stage:

```
stage_start → tool_selected → stage_end → … → result → completion
```

Stage events report real phase boundaries, not decoration — a stage that reports
"succeeded" did. Readiness is checked before the first event: if the deployed catalog
cannot answer the turn, the API says so honestly instead of opening a stage it cannot
finish. A stream that visibly starts working and then dies reads as a broken product; an
honest "unavailable" reads as a deployment nobody has fed yet.

The frontend renders the frame as a table with units in the column headers, a Plotly chart
whose axis assignment is derived from those units rather than chosen by a model, and a
per-column provenance drill-down.

### The three published turns

The brief's worked scenario, implemented as `agent/turn1.py`, `turn2.py`, `turn3.py`:

1. **Housing loans and their rate, 60 months.** The question contains a false premise —
   rates roughly doubled over the window — and a definitional slip: it asks for *hacim*, and
   no source publishes gross new lending. The turn tests the premise rather than repeating
   it, answers over the window where rates *did* fall, and states that the figure is a
   balance outstanding, not lending extended.
2. **Deflate the loan column without breaking the table.** Adds CPI, deflates, spine intact.
3. **Add the house price index as a new column, and revise the earlier finding.** The
   original finding is superseded, not overwritten.

---

## Stack

Python 3.11+, per the brief. FastAPI, pydantic, DuckDB, pandas, LanceDB, Plotly, pypdf,
ruff, pytest. Next.js on the frontend. **809 tests**, green, with the invariant suite as a
separate CI step so a data-correctness failure is legible as one.

The only runtime model provider is **MIA / Kloudeks** (`Qwen3` for planning,
`Qwen3-Embedding-8B` for retrieval). Third-party LLM APIs are excluded by the brief and the
dependency list carries a standing note saying so.

## Reproducibility

The published snapshot is `data-2026-09-15` (commit `dc2f53d`), fingerprint
`325ca5b2cfd660b5`. `scripts/build_catalog.py` reproduces it from the same bronze without
network access; `scripts/package_snapshot.py` builds the release archive and refuses to
package a catalog that cannot answer the published questions.
