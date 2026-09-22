# Selective hybrid retrieval: implementation and rollout evaluation

> Historical report. Latest refinement: [Concept-family evaluation](concept-family-retrieval.md).

Verdict: **SELECTIVE VECTOR RETRIEVAL NOT READY**.

Base HEAD `d820263`, branch `main`. Existing uncommitted closure, hybrid and latency
work was preserved. No commit/push, data/index rebuild, .env change, frontend change,
or published golden-contract update was made.

## A. Current production policy

Default remains **off**. `VECTOR_RETRIEVAL_MODE=off|selective|always` is an optional
server-side override. Unset preserves `VECTOR_RETRIEVAL_ENABLED=false -> off` and
`true -> always`. Explicit mode takes precedence. Existing deployments do not
silently opt into a new policy. `always` retains the previous experimental behavior;
it does not acquire the new selective safety gate.

Selective is wired into the real generic production runner. Published turns bypass
retrieval completely. Canonical exact IDs bypass embeddings. Strong lexical results
bypass embeddings. Weak/absent lexical results attempt existing hybrid enrichment.
No MIA routing/planning fast path or timeout was changed.

## B. Lexical confidence

No fitted score threshold. The shortcut requires existing harmonic score **exactly
1.0** (complete exact token coverage), no substitution, and exactly one fully
metadata-compatible catalog row equal to the lexical selection. All other resolved
lexical results are weak; no lexical result is absent.

This is intentionally restrictive and **does not recognize the familiar gold-catalog
queries as strong**. Token coverage alone cannot prove scope/version equivalence.
The implementation needs a narrower, justified financial candidate-family boundary
before this shortcut is suitable for broad production use. It is not claimed to be
a calibrated confidence measure.

## C. Semantic acceptance

Existing vector candidates, union, hard filters, ranking and deterministic resolver
remain in use. Selective acceptance additionally requires a single compatible catalog
identity matching the resolver output. With multiple compatible identities it returns
`RetrievalAmbiguity(reason="financial_identity_ambiguous", candidate_ids,
differing_fields, user_message)` and clears selected IDs.

The check considers the **whole metadata-compatible catalog**, not merely vector
top-k. Otherwise top-k truncation could hide a conflicting version. This avoids that
false assurance, but is too broad: unrelated series sharing a measure type may also
block selection. This is an implementation limitation, not evidence that every
catalog series listed is a genuine semantic equivalent.

## D. Ambiguity policy

CPI variants are not assigned an invented preferred base. Source/scope variants with
missing scope are not declared equivalent. Unknown explicitly requested metadata is
rejected. Different index versions and stock/flow/scope/frequency conflicts have
negative tests. Exact-ID requests remain available for an explicitly chosen series.

The existing safe tool-refusal/SSE path displays a fixed Turkish clarification message.
No wire schema, frontend model, embedding score or vector array was added. The generic
analyze boundary raises the dedicated `AmbiguousSeries` refusal; the router catches
only that controlled exception to produce the user message.

## E. Live fixed evaluation

Read-only real MIA + existing 291-row LanceDB/DuckDB catalog, same eight questions.
Exact expected IDs were not changed. `SAFE_AMBIGUITY` describes the safety outcome,
not acceptance success: only original cases 3/5/7 allow it. Cases 1/2/4/6/8 still require
exact matches. Evaluator exits **1** because those five requirements are unmet.

| Query | Lexical outcome | Vector invoked | Vector top three | Final outcome | Policy acceptance |
|---|---|---|---|---|---|
| konut kredisi bakiyesi | bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut | yes | evds.TP.KM.B11, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut, evds.TP.KB.KRE10 | SAFE_AMBIGUITY | FAIL: exact required |
| konut kredisi faiz oranı | evds.TP.KTF12 | yes | evds.TP.KTF12, evds.TP.BKR.TRY.18, evds.TP.KB.KRE10 | SAFE_AMBIGUITY | FAIL: exact required |
| tüketici fiyat endeksi | evds.TP.FE25.OKTG01 | yes | evds.TP.FE25.OKTG01, bddk_aylik.t04.taraf10001.takipteki_t_ketici_krd, evds.TP.GENENDEKS.T1 | SAFE_AMBIGUITY | PASS |
| konut fiyat endeksi | evds.TP.KFE.TR | yes | evds.TP.KFE.TR, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut_d_vize_endeksli, evds.TP.FE25.OKTG01 | SAFE_AMBIGUITY | FAIL: exact required |
| Ev satın almak için bankalara kalan borç bakiyesi | NO_RESULT | yes | evds.TP.KB.KRE10, evds.TP.KM.B11, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut | SAFE_AMBIGUITY | PASS |
| Ev satın alma kredisinin faiz maliyeti | NO_RESULT | yes | evds.TP.KTF12, evds.TP.BKR.TRY.18, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut | SAFE_AMBIGUITY | FAIL: exact required |
| Hanehalkının alışveriş sepeti fiyat endeksi | evds.TP.KFE.TR | yes | evds.TP.FE25.OKTG01, evds.TP.KM.B09, evds.TP.KM.E040 | SAFE_AMBIGUITY | PASS |
| Türkiye'de evlerin satış fiyatlarını izleyen endeks | NO_RESULT | yes | evds.TP.KFE.TR, evds.TP.KM.B09, evds.TP.KM.E040 | SAFE_AMBIGUITY | FAIL: exact required |

## F. Safety metrics

- EXACT_CORRECT: **0**
- SAFE_AMBIGUITY: **8**
- WRONG_SELECTION: **0**
- NO_RESULT: **0**

Zero wrong selections alone is insufficient. This implementation abstains excessively.

## G. Vector value

Live embeddings still surface KTF12 for “Ev satın alma kredisinin faiz maliyeti” and
HPI for the house-sale-price paraphrase where lexical has no result. However, neither
passes the conservative acceptance gate. **No live gold-catalog incremental exact
resolution was demonstrated.** Offline singleton-compatible fixtures demonstrate
successful semantic recovery and normal browser rendering, not production readiness.

## H. Latency

Published flow still invokes no retrieval/embedding. Offline strong/exact tests assert
zero provider calls; there is no gold-query strong-path latency claim.

Live selective timings, milliseconds (single run, first query includes cold setup):

| Query number | Embedding | LanceDB | Rerank | Total |
|---|---:|---:|---:|---:|
| 1 | 651.33 | 18.11 | 5.56 | 689.26 |
| 2 | 73.47 | 7.53 | 2.08 | 93.85 |
| 3 | 73.83 | 6.73 | 1.34 | 88.75 |
| 4 | 72.20 | 6.58 | 1.22 | 86.66 |
| 5 | 72.75 | 5.94 | 17.37 | 123.54 |
| 6 | 71.36 | 5.75 | 11.48 | 103.98 |
| 7 | 73.30 | 6.02 | 1.38 | 87.44 |
| 8 | 71.16 | 5.81 | 11.02 | 102.39 |

No model-routing wait was introduced by retrieval. These timings end in ambiguity,
not a successfully resolved frame, and are not a latency SLA.

## I. Failure/fallback and trace

Missing/stale/unavailable index, malformed vectors and provider failures keep existing
lexical fallback (or its existing no-result). Tests cover sanitized failure codes and
missing explicit metadata. This intentionally retains old lexical behavior: it is
**not** a claim that legacy lexical selection is financially disambiguated. The initial
network-restricted run fell back to lexical: 3 exact, 2 wrong selections relative to the
fixed expected IDs, 3 no-results. The approved live run above used real embeddings.
That fallback limitation is another reason not to declare universal financial safety.

Internal trace adds mode, lexical confidence, vector-invoked and ambiguity flags to
existing counts, IDs, safe reasons and timings. No embeddings or raw exception details
are logged or transported.

## J. Validation

- New selective tests: **24 passed**.
- Catalog/agent/API/LLM integration before final three additive tests: **493 passed**.
- Full backend: **974 passed, 7 skipped, 1 warning** (15.08 seconds).
- Ruff: **PASS**. Format: **168 files already formatted**. `git diff --check`: **PASS**.
- Fixed regression: **PASS**, exact IDs, 60 months, versions **2 -> 4 -> 5**, lineage
  and findings unchanged. No golden diff.
- Real offline browser smoke: **PASS**, including new real-LanceDB/fake-embedding
  semantic result, ambiguity and preservation of the previous successful frame.
- Existing 7 network opt-in skips remain (6 acceptance-live, 1 browser render).
- Existing Starlette/AnyIO warning remains.

Commands:

```sh
.venv/bin/python scripts/evaluate_vector_retrieval.py
.venv/bin/python -m pytest tests/kkb_agent/catalog/test_selective_retrieval.py -q
.venv/bin/python -m pytest -q -rs
.venv/bin/python scripts/run_regression.py
.venv/bin/python scripts/browser_smoke.py
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
git diff --check
```

## K. Default configuration and changed files

**Selective is not enabled by default.** Opt-in only via
`VECTOR_RETRIEVAL_MODE=selective`, followed by backend restart. .env was not edited
and the existing development backend was not restarted.

Task changes:

- `src/kkb_agent/catalog/hybrid.py`: selective gate, typed ambiguity, trace.
- `src/kkb_agent/config.py`, `.env.example`: optional compatible mode override.
- `src/kkb_agent/api/main.py`: mode-aware runner composition/health enabled flag.
- `src/kkb_agent/agent/analyze.py`, `agent/router.py`: safe ambiguity refusal.
- `scripts/evaluate_vector_retrieval.py`: policy classifications, per-case acceptance.
- `scripts/browser_smoke.py`: offline semantic success/refusal scenarios.
- `tests/kkb_agent/catalog/test_selective_retrieval.py`: invocation/safety/config tests.
- `docs/selective-retrieval.md`: this report.

Existing index, embedding provider, financial resolver, ranking weights, MIA planner
fast paths, published operations, frame/SSE contracts and fixture snapshots unchanged.
Local data and generated artifacts are not added; live evaluation output is in /tmp.

## L. Remaining blockers

**SELECTIVE VECTOR RETRIEVAL NOT READY**.

1. Narrow the overly broad ambiguity candidate set using justified concept/family
   evidence. The current full compatible-catalog gate safely refuses unrelated as well
   as related peers and does not meet the required strong-query shortcut behavior.
2. Establish safe handling of materially distinct rate definitions, CPI base/version,
   and missing stock source/scope semantics without fabricating metadata or preferences.
3. Demonstrate live incremental semantic exact resolution and all five required exact
   cases before default rollout. Keep wrong selections at zero; do not loosen the gate
   solely to match the eight examples.
4. Legacy lexical fallback can still disagree with expected financial identities;
   preserving fallback was required and does not itself solve that pre-existing issue.
