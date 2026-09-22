# Demo latency hardening — measured before and after

2026-09-23 · base HEAD `d820263` · branch `main`.

**READY FOR LIVE DEMO** for the supported, measured flows. Hybrid retrieval remains
**disabled by default** and its separate readiness verdict is unchanged.

## A. Root causes

Measurements preceded behavior changes. `scripts/profile_demo_latency.py` starts an
isolated production FastAPI app with the real local gold database, real configured MIA,
real SearxNG and real URL fetcher, then consumes `/ask` through an actual streaming HTTP
socket. One app/store serves the three published turns under the same analysis ID.
No replay or mocked provider was used for these measurements.

The same eight questions were sent sequentially before and after the patch. All sixteen
requests completed successfully. Times use `perf_counter()`; service readiness uses
`monotonic()`. No internal timing text is added to user answers. These are single-run
observations, not percentile measurements or a latency SLA.

- **Turn 1:** 26.168 s total, of which 26.085 s was two model completion calls to emit
  already-known operations. Its 25-second planning budget checked elapsed time between
  attempts; it did not cancel an outstanding HTTP request.
- **Turns 2/3:** already deterministic; 24/15 ms before. No model latency to remove.
- **Generic:** 8.951 s model routing followed by 14.788 s model planning. Lexical
  resolution took 25 ms. The source-only operation sequence was already determined
  by the resolved catalog rows.
- **Anomaly:** 7.198 s model routing; actual analytical tool computation 4.35 ms.
- **Change detection:** 12.577 s model routing; actual computation 22.68 ms.
- **Web:** 7.381 s model routing plus 3.111 s SearxNG. Search remained a real external
  operation; the redundant wait was routing.
- **URL:** 9.449 s model routing, 0.699 s fetch, 2.75 ms content processing.
- **SSE:** generic routes awaited model routing *before* their first stage event.
  Serialization itself was sub-millisecond per request.
- **LanceDB:** not in these request paths at all: vector retrieval was disabled.

The installed OpenAI SDK previously defaulted to a 600-second read/write/pool timeout,
5-second connect timeout, and two automatic retries. Those defaults were unsuitable
for the remaining model-assisted demo paths.

## B. Files changed for this task

New:

- `scripts/profile_demo_latency.py`: opt-in live profiling; read-only gold access.
- `tests/kkb_agent/api/test_latency.py`: 18 offline deterministic tests.
- `docs/demo-latency-hardening.md`: this report.

Updated:

- `src/kkb_agent/llm/client.py`: explicit HTTP phase timeouts and zero SDK retries.
- `src/kkb_agent/agent/router.py`: narrow explicit-intent fast paths; keep ambiguity
  eligible for model routing; bounded source-only dispatch skips redundant planning.
- `src/kkb_agent/agent/planner.py`: safe timeout/provider error types/messages.
- `src/kkb_agent/agent/analyze.py`: safe internal logging of timeout/error fallback;
  explicit model-planning API and existing source-only validation remain available.
- `src/kkb_agent/api/turn1_runner.py`: published Turn 1 uses its existing fixed plan;
  generic routing starts its stage before waiting; concise planning attribution.
- `tests/kkb_agent/api/test_turn1_runner.py`: refusal now closes its routing stage.
- `tests/kkb_agent/agent/test_planner.py`: sanitized provider-error expectation.
- `tests/kkb_agent/llm/test_client.py`: verify actual client timeout/retry options.

The working tree already contained earlier frontend, SSE, hybrid, CI and closure work.
Those changes are not new latency work. No frontend, frame/operation schema, ranking,
metadata, golden fixture or analytical-tool implementation was edited for this task.

## C. Fast paths

- All three deterministically recognized published turns skip MIA routing, planning
  and embeddings. Turn 1 calls its existing scripted typed-operation implementation;
  Turns 2/3 keep their existing transformations.
- A URL uses the existing URL rule before model routing.
- A single explicit anomaly/change/causality/web-search cue routes deterministically.
  A generic display verb does not shadow a more specific analytical cue.
- Conflicting specialist cues still allow model routing. Questions without confident
  cues remain eligible for MIA; model planning was not removed globally.
- Generic display routing only skips MIA when existing display cues also name a
  narrow financial subject (credit, deposits, interest, housing, CPI, capital).
  A display verb by itself, such as “Hava durumunu göster”, is not enough.
- Once the bounded generic source-only path resolves its rows, dispatch uses its
  existing deterministic `add_column` sequence instead of asking MIA to reproduce it.
  `OperationPlanner` and explicit `analyze(planner=...)` remain usable. Max three
  series, bounded dates, source-only plan validation and fallback are unchanged.

Internal routing attribution distinguishes `rules`, `planner`,
`model_timeout_fallback` and `model_error_fallback`. Optional planner fallback logs
only these safe path codes. Existing retrieval trace still distinguishes lexical,
hybrid and lexical fallback. The published answer no longer dumps operation strings;
it says “Analiz deterministik plan ile tamamlandı.” when that is what ran.

## D. Actual model timeouts and application budget

Central MIA client defaults now explicitly configure:

| HTTP phase | Timeout |
|---|---:|
| Connect | 5 seconds |
| Read | 35 seconds |
| Write | 10 seconds |
| Connection pool | 5 seconds |
| Hidden SDK retries | **0** |

Healthy baseline routing took 7.2–12.6 s and a successful generic plan about 14.8 s.
A 35-second read timeout leaves headroom instead of forcing normal healthy requests
into routine fallback. Embeddings retain their existing separate 15-second override
and zero retries; ranking and index behavior are unchanged.

**These are real HTTP I/O timeouts, not a strict whole-turn wall-clock deadline.**
Read timeout limits waiting for network data; it is not a cumulative timer across
multiple calls, redirects or response chunks. No speculative background request is
launched and abandoned to pretend a faster result occurred.

The standalone Turn 1 helper's existing 25-second application budget remains an
attempt-admission check. The served published runner now bypasses that model loop
entirely. Direct callers who explicitly request model planning still get actual HTTP
timeouts, and `OperationPlanner` allows at most one correction retry after an invalid
structured response. A transport/model failure is not retried by that planner.
The optional helper's existing three-attempt ceiling is not a new HTTP retry policy.

An SDK `APITimeoutError` maps to existing routing rules or, where no rule applies, an
honest refusal. Explicit operation planning raises sanitized `PlannerTimeoutError`;
the existing `analyze` fallback may then build its deterministic source-only result.
Transport tests exercise the real SDK with a mocked HTTP `ReadTimeout`, inspect
request timeout extensions, and verify **one transport call**, safe errors and
unchanged prior stored frames. No test waits for a live model timeout.

## E. SSE responsiveness

The first generic `agentic_analytics` stage is now yielded before model/tool selection
starts. Refused questions close that stage with `failed` before error/completion.
Tools run after selection while the stage stays visibly open. Existing transport
exception handling closes an open stage and emits sanitized failure/completion.
No wire enum or SSE schema changed; no-cache and no-buffering headers remain intact.

Offline tests hold a model/search boundary on a synchronization event and prove the
first serialized stage was yielded **before the dependency even started**, remains
visible while it blocks, and closes exactly once after release. The live benchmark
also consumes real incrementally delivered HTTP events.

| Path | First SSE before, ms | First SSE after, ms |
|---|---:|---:|
| Turn 1 | 16.57 | 20.36 |
| Turn 2 | 3.27 | 0.98 |
| Turn 3 | 1.09 | 0.82 |
| Generic | 8953.59 | 0.68 |
| Anomaly | 7200.38 | 0.67 |
| Change detection | 12578.75 | 0.75 |
| Web | 7382.98 | 0.80 |
| URL | 9451.39 | 1.00 |

Published Turn 1 already streamed before planning. Its few-millisecond first-event
variation is not an improvement or regression claim.

## F. Same-query before/after timings

Queries, in order:

1. “Konut kredisi bakiyeleri ile konut kredisi faizlerini 2021-2025 arasında göster.”
2. “Konut kredisi tutarını TÜFE ile reel hale getir.”
3. “Konut fiyat endeksini de ekleyip önceki bulguyu yeniden değerlendir.”
4. “2022 ile 2024 arasında konut kredilerindeki değişimi göster.”
5. “Konut kredisi serisinde anomalileri bul.”
6. “Konut kredilerinde belirgin kırılma noktaları var mı?”
7. “TCMB'nin güncel duyurularını web'de ara.”
8. “https://example.org/ oku” — same host as the browser smoke's fake document;
   its live root was used in **both** runs because the fixture's `/report` is not a
   real published document.

All figures below are seconds, end-to-end backend HTTP/SSE consumption:

| Path | Before | After | Time saved | Main remaining cost |
|---|---:|---:|---:|---|
| Turn 1 | 26.1678 | 0.0410 | 26.1268 | Catalog/frame construction |
| Turn 2 | 0.0243 | 0.0128 | 0.0115 | CPI read/deflation/validation |
| Turn 3 | 0.0151 | 0.0117 | 0.0033 | HPI read/finding revision |
| Generic | 23.8130 | 0.0309 | 23.7822 | Lexical resolution/catalog |
| Anomaly | 7.2740 | 0.0396 | 7.2345 | Resolution/series reads |
| Change detection | 12.6712 | 0.0584 | 12.6128 | Resolution/computation |
| Web | 10.5268 | 3.1542 | 7.3726 | SearxNG: 3.1421 s |
| URL | 10.1717 | 0.1278 | 10.0439 | Website fetch: 0.1160 s |

Turn 2/3 were not optimized; their small differences are ordinary local variation.
URL fetch also varied from 0.699 to 0.116 s; only removal of 9.449 s routing is
attributable to this patch. No application search/URL result cache was added.

Boundary breakdown in milliseconds follows. Durations are **inclusive**, so do not
sum routing with its nested model wait, or operation execution with nested fetches.
`retrieval` sums observed catalog/spine/source reads and generic/tool-series resolution.
`execution` is executor plus analytical-tool/finding boundaries; executor includes
validation and nested source reads, so it is not a pure-arithmetic microbenchmark.

| Run/path | Routing | Model wait | Retrieval | Execution | External | Result at | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| before/turn1 | 0.00 | 26085.16 | 19.37 | 25.57 | 0.00 | 26166.90 | 26167.84 |
| before/turn2 | 0.00 | 0.00 | 16.09 | 3.94 | 0.00 | 23.81 | 24.29 |
| before/turn3 | 0.00 | 0.00 | 10.28 | 2.09 | 0.00 | 14.62 | 15.06 |
| before/generic | 8950.72 | 23738.25 | 40.66 | 7.03 | 0.00 | 23812.60 | 23813.03 |
| before/anomaly | 7198.09 | 7197.98 | 65.72 | 5.68 | 0.00 | 7273.79 | 7274.02 |
| before/change | 12577.04 | 12576.99 | 66.41 | 23.91 | 0.00 | 12671.01 | 12671.24 |
| before/web | 7380.85 | 7380.81 | 0.00 | 0.00 | 3110.59 | 10526.28 | 10526.81 |
| before/url | 9448.60 | 9448.54 | 0.00 | 0.00 | 701.55 | 10171.31 | 10171.70 |
| after/turn1 | 0.00 | 0.00 | 11.38 | 5.01 | 0.00 | 40.72 | 41.00 |
| after/turn2 | 0.00 | 0.00 | 8.02 | 2.59 | 0.00 | 12.48 | 12.82 |
| after/turn3 | 0.00 | 0.00 | 7.80 | 1.59 | 0.00 | 11.42 | 11.75 |
| after/generic | 0.02 | 0.00 | 21.50 | 1.42 | 0.00 | 30.68 | 30.88 |
| after/anomaly | 0.01 | 0.00 | 32.89 | 4.03 | 0.00 | 39.36 | 39.55 |
| after/change | 0.01 | 0.00 | 35.63 | 19.82 | 0.00 | 58.18 | 58.43 |
| after/web | 0.01 | 0.00 | 0.00 | 0.00 | 3142.07 | 3153.98 | 3154.19 |
| after/url | 0.02 | 0.00 | 0.00 | 0.00 | 116.67 | 127.65 | 127.80 |

Request schema validation was separately probed at roughly 0.004–0.127 ms. This is
`AskRequest.model_validate` on the same payload, **not** an isolated measurement of
FastAPI's internal request parsing/network stack. Intent classification was below
0.14 ms/request; cumulative SSE serialization 0.05–0.27 ms/request.

URL processing includes content-type detection. Playwright/OCR were not invoked for
the measured static HTML page, so their latency is **unmeasured**, not reported as a
zero-cost fallback. Their implementations/security policies were untouched. The
current served URL composition reads fetched content; this task adds no new rendering
or OCR orchestration.

Reproduce against configured local services (live MIA/search/site access may be used):

```bash
.venv/bin/python scripts/profile_demo_latency.py --label current
```

Benchmark stdout is JSONL with timings, event stages and normalized analytical hashes.
It does not print credentials, mutate gold, or write fixtures. The isolated server
shuts down afterward; existing developer server processes were not restarted.

## G. Optional hybrid measurement

Only the measurement process used `Settings(vector_retrieval_enabled=True)` and the
production retrieval composition. No `.env` edit or permanent toggle was made.

| Query | Exact selected ID | Embedding, ms | LanceDB, ms | Rerank, ms | Total, ms |
|---|---|---:|---:|---:|---:|
| konut kredisi bakiyesi | `bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut` | 566.46 | 17.95 | 5.36 | 604.03 |
| Ev satın alma kredisinin faiz maliyeti | `evds.TP.KTF12` | 77.49 | 6.89 | 13.38 | 117.96 |

The first request includes lazy client/connection overhead. The normal configuration
was checked afterward: **`vector_retrieval_enabled=False`**. Its existing 5/8 evaluation
was neither rewritten nor rerun as an alleged new 8/8 pass. Metadata, ranking,
expected exact IDs and its documented unresolved semantics are unchanged.

## H. Correctness

**NO analytical value changes.**

- Fixed-snapshot regression: **PASS**, unchanged expected contract.
- Published exact series IDs and 60 monthly rows retained; versions **2 → 4 → 5**.
- Before/after normalized hashes of spine, columns and findings matched for all six
  frame-producing benchmark requests. Timestamp/frame identity bookkeeping is excluded.
- Generic 2022–2024 window remains 36 rows; source-only/max-three behavior tested.
- Anomaly/change/causality algorithms were not edited; existing deterministic tests
  and refusal-policy tests pass. Frame hashes alone are not a causality methodology test.
- Web/URL informational contracts remain unchanged and completed successfully in
  both live runs. Dynamic external search content is not claimed to be byte-identical.
- Tests verify a timed-out ambiguous request leaves the previous successful stored
  frame byte-identical; append-only history is not reset or truncated.

## I. Tests and checks

- New latency tests: **18**; combined focused latency/router/planner/runner/client
  run: **99 passed**.
- API + agent + catalog + LLM suite, including hybrid: **472 passed**.
- Full backend: **950 passed, 7 skipped, 1 warning**.
- Fixed regression: **PASS** (spine/versions, Turn 1, deflation/lineage, HPI/revision).
- Existing real browser smoke: **PASS** — 2 → 4 → 5, live SSE, charts/table,
  web/URL success, safe failures, state preservation and generic dates.
- Ruff: **PASS**; format: **167 files already formatted**.
- `git diff --check`: **PASS**.
- Frontend typecheck/build: not rerun for this task because no frontend file changed;
  browser smoke used the existing production build.

The seven skips remain six opt-in URL live-acceptance tests and one live-browser URL
render test (`KKB_NETWORK_TESTS=1` not enabled). One existing Starlette/AnyIO
DeprecationWarning remains. New tests require neither developer gold nor internet,
Docker, MIA or SearxNG; they use the fixed snapshot and mocked provider/HTTP boundaries.

## J. Remaining external latency

- **MIA:** only ambiguous routing or explicitly model-assisted APIs need it; actual
  HTTP phase limits and retry limits apply. It is not needed for the eight measured
  after-paths. Embedding still costs network/model time when explicitly enabled.
- **SearxNG:** approximately 3.14 s in the after run; fetching fresh evidence is still
  necessary. No fake/cached answer substitutes for a provider outage.
- **Website fetch:** approximately 0.116 s in the after run; depends on the site.
  Existing URL fetch limits remain. Render/OCR paths can cost substantially more;
  they were not measured by the static-page benchmark and are not promised subsecond.
- **Local app:** supported numeric paths measured 12–58 ms here. Host load, catalog
  size and cold start may vary; these results are not hard production latency bounds.

## K. Verdict

**READY FOR LIVE DEMO** for the supported measured flows, with hybrid retrieval left
disabled and the existing external-service failure behavior retained.

The model was bypassed only where the intent/operations were already determined.
No financial output, metadata or golden expectation was adjusted for speed. Commit
and push were not performed. Restart an already-running old backend process to serve
these changes; the benchmark's isolated app tested the current code directly.
