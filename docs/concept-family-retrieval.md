# Concept-family bounded selective retrieval

**SELECTIVE VECTOR RETRIEVAL NOT READY** — default remains off.
Base HEAD: `d820263`, branch main. No commit/push or .env edit.

## A. Cause of previous over-abstention

The old gate counted the whole metadata-compatible catalog: unrelated stocks,
rates and indices blocked each other. It returned eight ambiguities and no exact
results. The new gate operates on label families and yields two justified exact
results, six ambiguities and zero wrong selections in the live fixed evaluation.

## B. Deterministic family construction

`catalog/candidate_family.py` reuses existing Turkish tokenization and prefix
matching. Label tokens omit digits, short abbreviations (fewer than four characters)
and stock/flow qualifiers `stok`/`akım`. The latter omission does NOT equate those
rate definitions: it deliberately retains both as competing identities. Descriptive
qualifiers such as `genel` remain.

Rows belong to a seed's family when they share the existing exact source/raw-label
concept, or the shorter normalized label contains at least two tokens and every
one matches a token of the longer label using the existing stem relation.

Seeds for lexical assessment are the lexical resolver's selected IDs. After vector
search, seeds are the deterministic resolver's selection plus the nearest
metadata-compatible vector candidate. Disagreement is preserved as competing
families, rather than overruling one signal with the other. Expand one hop against
all hard-compatible catalog rows, then sort exact IDs. No transitive closure, generic
unit grouping, new embeddings, new vector scan, fabricated aliases or expected IDs.
This catches label-related siblings outside top-k without scanning vectors again.

This is a conservative label-evidence heuristic, not proof of ontology completeness.
Unrelated labels can still share a shorter aggregate label; semantically equivalent
labels with no token relationship may still need authoritative catalog family
metadata. These are limitations, not claims of financial equivalence.

## C. Strong lexical acceptance

All nonnumeric query subject tokens, excluding existing measure-intent cues, must
match the selected label, with at least two subject tokens. Exactly one identity
must remain in the bounded family, and no substitution may be present. No fitted
score threshold. HPI's ordinary wording now takes this zero-embedding shortcut.
Housing balances and rates still have related, materially different identities.

## D. Semantic acceptance

Existing union, hard metadata filters, scores and resolver stay unchanged. Accept
only when the bounded family is a singleton equal to the resolver selection.
The HPI sales-price paraphrase now resolves correctly where lexical has no result.
No top-1-only financial decision and no extra model call.

## E. True ambiguity and remaining limitations

CPI `Tüketici Fiyat Endeksi (Genel)` includes `Genel Endeks (2003=100)` as a sibling;
HPI does not join that family. Missing housing source/scope remains unresolved.
The two rate labels explicitly say Akım vs Stok and have weekly vs monthly native
frequencies. Generic rate wording does not establish which definition is required.
No source preference or rate-definition preference was invented to force KTF12.
Some aggregate/subset loan labels also remain in the bounded family; refining that
requires justified subject/qualifier semantics, not tuning to the eight queries.

## F. Same eight-query live evaluation

Real existing 291-row catalog/index and MIA embeddings; no rebuild or data writes.
Full IDs appear below. Empty lexical result means no lexical resolution. Family IDs
are the actual internal trace. SAFE_AMBIGUITY is a safety classification; cases
1/2/4/6/8 still require exact results. Evaluation exits **1** (cases 1/2/6 unmet).

| Query | Lexical IDs | Vector invoked | Family IDs | Final classification |
|---|---|---|---|---|
| konut kredisi bakiyesi | bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut | True | bddk_aylik.t04.taraf10001.t_ketici_kredileri, bddk_aylik.t04.taraf10001.t_ketici_kredileri_d_v_end, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut_d_vize_endeksli, bddk_aylik.t04.taraf10001.takipteki_konut_kredileri, evds.TP.KB.KRE09, evds.TP.KB.KRE10, evds.TP.KM.B10, evds.TP.KM.B11 | SAFE_AMBIGUITY |
| konut kredisi faiz oranı | evds.TP.KTF12 | True | evds.TP.BKR.TRY.18, evds.TP.BKR.TRY.KTFTUK, evds.TP.BKR.TRY.KTFTUK01, evds.TP.KBK.TRY.KBTFTUK, evds.TP.KBK.TRY.KBTFTUK01, evds.TP.KKP.TRY.KTFTUK, evds.TP.KKP.TRY.KTFTUK01, evds.TP.KTF12, evds.TP.KTFTUK, evds.TP.KTFTUK01 | SAFE_AMBIGUITY |
| tüketici fiyat endeksi | evds.TP.FE25.OKTG01 | True | evds.TP.FE25.OKTG01, evds.TP.GENENDEKS.T1 | SAFE_AMBIGUITY |
| konut fiyat endeksi | evds.TP.KFE.TR | False | evds.TP.KFE.TR | EXACT_CORRECT |
| Ev satın almak için bankalara kalan borç bakiyesi | none | True | bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut, bddk_aylik.t04.taraf10001.t_ketici_kredileri_konut_d_vize_endeksli, bddk_aylik.t04.taraf10001.takipteki_konut_kredileri, evds.TP.KB.KRE10, evds.TP.KM.B11 | SAFE_AMBIGUITY |
| Ev satın alma kredisinin faiz maliyeti | none | True | evds.TP.BKR.TRY.18, evds.TP.BKR.TRY.KTFTUK, evds.TP.BKR.TRY.KTFTUK01, evds.TP.KBK.TRY.KBTFTUK, evds.TP.KBK.TRY.KBTFTUK01, evds.TP.KKP.TRY.KTFTUK, evds.TP.KKP.TRY.KTFTUK01, evds.TP.KTF12, evds.TP.KTFTUK, evds.TP.KTFTUK01 | SAFE_AMBIGUITY |
| Hanehalkının alışveriş sepeti fiyat endeksi | evds.TP.KFE.TR | True | evds.TP.FE25.OKTG01, evds.TP.GENENDEKS.T1 | SAFE_AMBIGUITY |
| Türkiye'de evlerin satış fiyatlarını izleyen endeks | none | True | evds.TP.KFE.TR | EXACT_CORRECT |

## G. Totals

EXACT_CORRECT **2**, SAFE_AMBIGUITY **6**, WRONG_SELECTION **0**, NO_RESULT **0**.
Target 5/3/0/0 was NOT reached. Successful exact cases are HPI direct wording and
its semantic paraphrase. Only the latter demonstrates incremental live vector value.

## H. Latency

Live retrieval milliseconds, single run (cold setup affects first embedding):

| Query | Family build | Embedding | LanceDB | Rerank | Total |
|---|---:|---:|---:|---:|---:|
| 1 | 4.79 | 536.16 | 17.50 | 8.43 | 577.12 |
| 2 | 1.06 | 320.52 | 11.16 | 3.05 | 345.77 |
| 3 | 0.30 | 75.15 | 7.26 | 1.36 | 92.17 |
| 4 | 0.18 | 0.00 | 0.00 | 0.00 | 2.54 |
| 5 | 1.65 | 74.21 | 6.08 | 18.83 | 125.15 |
| 6 | 0.53 | 224.73 | 10.02 | 16.63 | 266.92 |
| 7 | 0.34 | 75.76 | 6.92 | 1.55 | 91.52 |
| 8 | 0.25 | 70.80 | 6.37 | 11.15 | 103.67 |

Family timing sums pre-vector and post-vector building. Rerank includes post-vector
family work, so columns must not be summed as disjoint phases.
Published fixture-backed HTTP/TestClient latency smoke: version 2 **829.95 ms**
(cold Python/tool imports), version 4 **14.81 ms**, version 5 **11.90 ms**.
All succeeded, with zero calls to injected retrieval. This is a local smoke, not
a live browser SLA or an apples-to-apples performance regression benchmark.

## I. Validation

- Family tests: 5 added; existing selective tests: 24 retained.
- Catalog/agent/API/LLM: **501 passed**.
- Full backend: **979 passed, 7 skipped, 1 warning** (16.72 s).
- Seven unchanged opt-in network skips: six URL acceptance-live, one render.
- Fixed regression: **PASS**; exact IDs, 60 rows, versions 2 -> 4 -> 5,
  published values/lineage/findings unchanged. No golden edits.
- Browser smoke: **PASS**, including semantic success, true ambiguity and previous
  frame preservation. Its former CPI ambiguity expectation
  was invalid after narrowing: the tiny fixture has only one CPI. The test now adds
  an explicit same-concept CPI sibling to its TEMPORARY database after published
  turns, leaving the frozen snapshot and all real data untouched.
- Ruff and format: **PASS**, 170 files formatted. `git diff --check`: **PASS**.

Internal trace adds family IDs/reason/size, ambiguity competitors, decision source
(lexical_unique / semantic_unique / ambiguity), and family-building time. No SSE or
AnalysisFrame changes or frontend internals. Index/provider/lexical/resolver logic and
existing timeouts/fast paths are unchanged. Existing fallback remains lexical;
its pre-existing exact-identity disagreements are not solved by the family gate.

Files for this refinement:
- src/kkb_agent/catalog/candidate_family.py (new)
- src/kkb_agent/catalog/hybrid.py
- tests/kkb_agent/catalog/test_candidate_family.py (new)
- scripts/browser_smoke.py (temporary ambiguity fixture only)
- docs/concept-family-retrieval.md (this report)
- docs/selective-retrieval.md (supersession pointer)

## J. Default

Default **off**, unchanged legacy flag compatibility. Selective remains opt-in via
VECTOR_RETRIEVAL_MODE=selective. .env and running development services unchanged.

## K. Verdict

**SELECTIVE VECTOR RETRIEVAL NOT READY**.

Family bounding removes blanket abstention and demonstrates real semantic HPI
recovery, but exact housing-balance and rate acceptance is not justified yet.
Required remaining work: authoritative treatment of source/scope and flow-vs-stock
rate definitions, and tighter justified treatment of broader/subset loan labels.
Do not promote similarity or alphabetical tie-breaking into financial authority.
