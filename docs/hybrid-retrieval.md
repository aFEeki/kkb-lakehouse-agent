# Hybrid semantic catalog retrieval — implementation and evaluation

Audit date: 2026-09-23. Base HEAD: `d820263`, branch `main`.

**HYBRID VECTOR RETRIEVAL NOT READY for default production rollout.**
The implementation and offline integration checks pass, but the fixed live evaluation
matches the expected exact series in **5/8 queries**, not 8/8. The three remaining
exact-identity disagreements are recorded below. `VECTOR_RETRIEVAL_ENABLED` therefore
**defaults to false**; the existing lexical behavior stays active. The implementation
can be explicitly enabled for evaluation. No catalog aliases, financial metadata, or
published expected outputs were altered to force the evaluation to pass.

## A. Files changed

New files for this task:

- `src/kkb_agent/llm/embeddings.py`
- `src/kkb_agent/catalog/vector_index.py`
- `src/kkb_agent/catalog/hybrid.py`
- `scripts/build_vector_index.py`
- `scripts/evaluate_vector_retrieval.py`
- `tests/kkb_agent/catalog/test_vector_retrieval.py`
- `tests/kkb_agent/llm/test_embeddings.py`
- `tests/kkb_agent/api/test_hybrid_integration.py`
- `docs/hybrid-retrieval.md`

Modified for this task:

- `src/kkb_agent/catalog/series_resolver.py`: optional ranked candidates and strict
  constraint mode; original default behavior retained.
- `src/kkb_agent/config.py`, `.env.example`: optional retrieval switch and bounded top-k.
- `src/kkb_agent/agent/analyze.py`, `agent/router.py`, `api/turn1_runner.py`,
  `api/main.py`: explicit injection into generic analyze; optional index health status.
- `tests/kkb_agent/api/test_main.py`: additive health field assertion.

Several of these integration files were already dirty before this task. Pre-existing
CI, SSE schema/contracts, frontend, browser-smoke, date-window, informational-result,
Turn 1 test and end-to-end-closure changes remain in the working tree. They were not
introduced by vector retrieval. This task changes no frontend/SSE/AnalysisFrame/
Operation contract and does not commit or push.

## B. Index design

`embedding_text(row)` uses a fixed ordered whitelist of existing fields:
`series_id`, `name_tr`, `raw_label`, `source`, `measure_type`, `native_freq`,
`unit_normalized`, `unit_raw`, `scale_factor`, `sector_scope`, `province`,
`currency_basis`. Empty/null optional fields are omitted. The existing concept label
is represented by `raw_label`; no new conceptual aliases are fabricated.

There are no observations, observation counts, credentials, timestamps, or arbitrary
raw-row fields in the embedding text. Metadata is fetched read-only from DuckDB.
`nonzero_observations` is read separately for the existing resolver's data-availability
preference, but is not embedded.

LanceDB table `semantic_catalog` stores:

| Field | Type |
|---|---|
| series_id | string, canonical identity |
| vector | fixed-size float64 list; live model dimension 4096 |
| embedding_text | string |
| metadata_json | deterministic JSON of the same whitelist |
| fingerprint | SHA-256 |
| model_id | configured embedding model |
| index_version | `catalog-metadata-v1` |

The fingerprint covers sorted metadata content and index version; it is independent
of row order and runtime timestamps. A read verifies row count, unique ID set, model,
fingerprint, index version and vector shape before query embedding. Changes to
embedded metadata, identity, model or version cause lexical fallback. An observation
update alone does not invalidate metadata vectors.

All batches validate before a single table overwrite. Rebuilding replaces the logical
table rather than appending duplicate series. A failed embedding batch leaves the
previous table intact. The build verifies count and identity after writing. There is
no startup rebuild. Local LanceDB files are gitignored, as is `.env`.

## C. Embedding provider and commands

`EmbeddingProvider` exposes `model_id`, `embed_texts(texts)` and `embed_query(text)`.
`MIAEmbeddingProvider` reuses the existing lazy `MIAClient` and configured model/key;
there is no second hosted provider. Builds use batches of up to 64. Query embedding
uses one request, a 15-second timeout and zero SDK retries.

Validation checks result count, result indices/order, dimension, numeric finite values
and nonzero vectors. Provider exceptions become fixed safe error codes. No keys or
raw provider exception messages are printed.

Qwen query embeddings include an English task instruction followed by the Turkish
query, as specified by the [official Qwen model documentation](https://huggingface.co/Qwen/Qwen3-Embedding-8B).
Catalog documents do not receive that instruction. Other configured model families
receive the original query.

```bash
.venv/bin/python scripts/build_vector_index.py
.venv/bin/python scripts/evaluate_vector_retrieval.py
```

The evaluator is read-only and exits nonzero if its exact-ID expectations fail.
It does not rebuild the index, rewrite gold data or change any golden contract.

To opt into the experimental generic path, set `VECTOR_RETRIEVAL_ENABLED=true`
server-side and restart FastAPI. `VECTOR_TOP_K` defaults to 20 and is bounded to
1–100. No `.env` was edited for this task.

## D. Hybrid retrieval

Public internal entry point:

```python
HybridRetrieval(index, top_k=20).resolve(question, catalog_rows) -> RetrievalResult
```

The returned object contains the existing `SeriesResolution` and `RetrievalTrace`.
It is not a second financial resolver or a browser contract.

1. Existing concept search produces up to 30 lexical concepts with its existing
   `0.35` minimum score and measure-intent filter; expand these to exact series IDs.
2. One query embedding and exact cosine LanceDB scan produce bounded vector candidates.
   Search returns ID, distance and inspection metadata, not a selected financial series.
3. Union candidates by exact `series_id`. DuckDB metadata remains authoritative.
4. Before ranking, reuse existing scope/province/currency facet filters and apply
   explicit measure type, source and native-frequency constraints. A conflicting or
   absent required field rejects a candidate. Frequency uses catalog codes `M/W/D/Q`.
5. Feed ranked concept hits and the candidate union into existing `resolve_series`
   with strict mode, which cannot fall back to unfiltered incompatible rows.

Scoring is deliberately transparent:

- `L`: existing qualifying lexical harmonic score, already in `[0,1]`; 0 if not a
  qualifying lexical candidate.
- `S = clamp(1 - cosine_distance / 2, 0, 1)`; 0 without a vector candidate.
- Per eligible series: `score = (L + S) / 2`.
- Per concept: maximum eligible series score. A rejected facet cannot promote an
  eligible sibling's concept through its vector score.
- Existing has-data preference remains first; then descending score and exact
  series-ID tie-break. Within the chosen concept, existing deterministic sorted IDs
  and facet semantics remain unchanged.

These are equal normalized contributions, not weights tuned to force published IDs.
Exact flat scanning and sorting by distance/ID also make vector top-k boundary ties
stable. For this small catalog, all distances are inspected but only top-k candidate
metadata is returned; no vector arrays are sent to the browser. A substantially
larger catalog may warrant a separate performance evaluation.

The generic flow remains date parsing → retrieval → existing SeriesSource → existing
operations/calculations/findings. The maximum-three-series rule is unchanged.

## E. Fast paths

- Published Turn 1/2/3 do not enter generic retrieval, even when injected with an
  enabled retrieval component. A production API test verifies no retrieval call.
- With semantic retrieval enabled, canonical exact-ID requests bypass embedding;
  unknown exact IDs or conflicting explicit constraints resolve to no series.
- Identifiers are removed before interpreting facet words so the `TP` in an EVDS
  identifier cannot be mistaken for an explicit Turkish-lira scope request.
- Disabled mode returns the original lexical resolver result unchanged.
- Other generic natural-language queries use hybrid retrieval when enabled. An exact
  label alone is not an extra fast path; its embedding cost is measured below.

## F. Failure, readiness and trace

Missing/empty/inaccessible/corrupt/stale index, provider failures/timeouts and malformed
query embeddings produce `lexical_fallback` with a safe fixed reason. No exception
representation or traceback enters the trace/SSE. Disabled mode is `lexical`; a
successful vector enrichment is `hybrid`.

Trace records lexical/vector/union counts, final series IDs and embedding/search/
rerank/total times. It stays internal on `Analysis.retrieval_trace`; no SSE field or
frontend developer text was added.

`/health` keeps core readiness dependent on the existing core components and adds:

```json
{"semantic_index": {"status": "ready", "enabled": false}}
```

Status is `ready`, `empty` or `unavailable`. Opening a LanceDB connection alone does
not imply index readiness. `enabled` means configuration, not proof that a specific
request used vectors. Provider/network reachability is not probed by health.
Unavailable semantics alone cannot cause core health failure.

Fallback deliberately preserves existing lexical behavior, including its existing
substitution/disclosure policy; the stricter filtering applies to hybrid candidates.

## G. Real catalog evaluation

The fixed evaluation ran against all **291 local gold catalog rows** with real MIA
query embeddings. No observations or production DuckDB contents were changed.

Aliases used only to keep the report readable:

- **B** = `bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut`
- **R** = `evds.TP.KTF12`
- **C** = `evds.TP.GENENDEKS.T1`
- **C25** = `evds.TP.FE25.OKTG01`
- **H** = `evds.TP.KFE.TR`
- **KB** = `evds.TP.KB.KRE10`
- **KM** = `evds.TP.KM.B11`
- **NPL** = `bddk_aylik.t04.taraf10001.takipteki_konut_kredileri`

| Turkish query | Expected | Lexical selected | Vector top three | Hybrid selected | Exact-ID result |
|---|---|---|---|---|---|
| konut kredisi bakiyesi | B | B | KM, B, KB | B | PASS |
| konut kredisi faiz oranı | R | R | R, `evds.TP.BKR.TRY.18`, KB | R | PASS |
| tüketici fiyat endeksi | C | C25 | C25, `bddk_aylik.t04.taraf10001.takipteki_t_ketici_krd`, C | C25 | FAIL |
| konut fiyat endeksi | H | H | H, `bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut_d_vize_endeksli`, C25 | H | PASS |
| Ev satın almak için bankalara kalan borç bakiyesi | B | none | KB, KM, B | KB | FAIL |
| Ev satın alma kredisinin faiz maliyeti | R | none | R, `evds.TP.BKR.TRY.18`, B | R | PASS |
| Hanehalkının alışveriş sepeti fiyat endeksi | C | H | C25, `evds.TP.KM.B09`, `evds.TP.KM.E040` | C25 | FAIL |
| Türkiye'de evlerin satış fiyatlarını izleyen endeks | H | none | H, `evds.TP.KM.B09`, `evds.TP.KM.E040` | H | PASS |

Lexical top candidates for those queries respectively:

1. B, KB, KM, NPL.
2. R, `evds.TP.BKR.TRY.18`, `evds.TP.KTFTUK`, `evds.TP.KBK.TRY.KBTFTUK`,
   `evds.TP.BKR.TRY.KTFTUK`.
3. C25, H.
4. H, C25.
5. none.
6. none.
7. H, C25.
8. none.

The same evaluation command prints all exact IDs, top-five vector candidates and
per-query timings. It returned **exit 1**, honestly reflecting **5/8** exact matches.
Lexical alone matched **3/8**. Hybrid adds two correct paraphrase resolutions, but
that is insufficient to call the full requested evaluation complete.

## H. Latency

Measured locally with the real 4096-dimensional MIA query embedding endpoint,
20 vector candidates, and the existing 291-row index. Times in milliseconds:

| Component | Minimum | Median | Maximum |
|---|---:|---:|---:|
| Query embedding | 63.64 | 66.14 | 534.63 |
| LanceDB search and candidate serialization | 5.79 | 6.47 | 18.26 |
| Merge/filter/rerank | 1.11 | 3.57 | 17.17 |
| Total resolver, including lexical and readiness | 77.47 | 95.43 | 570.97 |

The first query includes lazy client/connection cost. These are observed timings,
not a latency SLA; a failed provider may consume the configured timeout. Published
turns and exact-ID fast paths do not pay query embedding latency.

## I. Tests and checks

- New focused embedding/index/hybrid/API tests: **43 passed**.
- Catalog + agent + API + new embedding tests: **450 passed**.
- Full backend: **932 passed, 7 skipped, 1 warning**.
- Fixed-snapshot regression: **PASS**.
- Existing live browser-to-local-API smoke: **PASS** (published 2 → 4 → 5, live SSE,
  charts/table, web/URL success, sanitized failure, state preservation, generic dates).
- Ruff: **PASS**. Format check: **165 files already formatted**.
- `git diff --check`: **PASS**.

The seven skips are the existing opt-in network tests: six in
`tests/kkb_agent/tools/url_agent/test_acceptance_live.py` and one in
`tests/kkb_agent/tools/url_agent/test_render.py` (`KKB_NETWORK_TESTS=1` not enabled).
The warning is the existing Starlette/AnyIO deprecation. No test was skipped because
of missing vector credentials: automated embedding tests are offline.

## J. Golden regression

**Expected published regression contract changed: NO.**
`tests/fixtures/regression` has no diff. Exact published series IDs, 60-row spine,
versions **2 → 4 → 5**, calculations, deflation lineage and finding supersession
remain covered by the unchanged regression. No embedding is required for those turns.

## K. Live semantic smoke

The real build completed with:

```text
Catalog rows: 291
Embedded: 291
Indexed: 291
Duplicates: 0
Result: PASS
```

Dimension: **4096**. Semantic index readiness: **ready**. Default/runtime semantic
retrieval configuration remains **disabled**, intentionally.

Example successful semantic improvement:
`Ev satın alma kredisinin faiz maliyeti` → `evds.TP.KTF12`, where lexical alone returned
no series. Observed total **96.25 ms**, including **63.69 ms** query embedding.

Local index files remain ignored under `data/gold/.lancedb`; `.env` remains ignored
and unchanged. No generated index, credentials or raw live-response artifact is added
to the version-controlled files. No Docker changes were required.

## L. Final verdict and exact blockers

**HYBRID VECTOR RETRIEVAL NOT READY** for default production rollout.

Implementation/test integration is complete, but these catalog/selection decisions
remain unresolved:

1. Generic CPI wording does not specify which base/version to use. The catalog has
   both `Tüketici Fiyat Endeksi (Genel)` (`evds.TP.FE25.OKTG01`) and
   `Genel Endeks (2003=100)` (`evds.TP.GENENDEKS.T1`). The task's expected latter ID
   cannot be guaranteed by label similarity. An authoritative preferred-version or
   ambiguity policy and corresponding catalog semantics are needed. The observed
   C25 choice is an exact-ID disagreement, not evidence that C25 is not a CPI index.
2. Housing-loan paraphrases can select `evds.TP.KB.KRE10` rather than the intended
   BDDK sector series. It and `evds.TP.KM.B11` have blank `sector_scope` in the local
   catalog. Their equivalence to the requested sector-wide stock has not been
   established. Populate/validate their scope metadata or adopt an explicit
   source-selection/ambiguity decision; do not promote similarity to authority.

After those authoritative decisions are supplied, rerun the unchanged evaluation
and tests before enabling hybrid retrieval by default. This task does not invent
those financial semantics, edit source data, tune to hardcoded published IDs or
claim the three failed exact-match cases passed.
