# Live backend/frontend integration

The published three-turn flow retains its 60 monthly rows, series IDs and versions
2, 4 and 5. The instance-local frame store still requires an exact current version
for published continuations. Independent tools do not update that published snapshot.
Informational results carry the requested identity/version but do not create a frame.
The browser continues only from the most recent successfully completed frame.

## Result wire extension

`POST /ask` request and event names are unchanged. `ResultPayload` has exactly one of:

- `frame`: the existing AnalysisFrame, plus `answer`;
- `information`: `tool`, `evidence` (title, HTTP(S) URL, optional snippet), and
  `caveats`, plus `answer`. `frame` is null in this case.

The committed SSE JSON schema is regenerated. Existing frame messages remain valid;
clients that assumed every result necessarily contained a frame must support the new
variant before consuming informational results. No AnalysisFrame/Operation schema changes.

Web/URL success emits result then successful completion. Expected refusals retain the
safe error/failed-completion path. Unexpected failures are sanitized by the transport,
which closes any open stage. Provider exception text is not public output. Source text
is untrusted evidence, rendered as text; links only use HTTP(S) with noopener/noreferrer.

Numerical responses use the existing chart-selection tool for initial chart specs and
show the real frame table, evidence-derived answer and per-column source references.
Non-frame results show source titles, links, snippets and extraction metadata.

## Bounded dates and narratives

Generic analysis supports year ranges (`2021-2025`, `2021 ile 2025 arasında`,
`2022'den 2024'e`), a single year, and Turkish named-month ranges
(`Ocak 2022 - Aralık 2024`). No explicit year preserves the existing 2021-01-01 to
2026-06-30 default. Reversed/ambiguous ranges are refused. The requested range is shown
in the answer and limits source observations; no observations are invented or filled.
Relative expressions are not parsed. This remains a maximum-three-series first/last
comparison, not an unrestricted analytics engine.

NarrativeGenerator remains independently available; the served path uses computed
findings and deterministic summaries to avoid adding an unnecessary model call or
unsupported claims. Generic scripted fallback is visible. No LLM arithmetic is introduced.

## Dependencies and data

Health's core status depends on application and usable DuckDB catalog readiness.
LanceDB connection status is retained for compatibility but is optional; it does not
mean vector retrieval is active. SearxNG and URL/browser dependencies are exercised on
demand; provider unavailability is reported by the relevant tool, not core health.
Health does not certify that the entire production dataset passed all data invariants.

Do not run every acquisition script blindly. Existing bronze/silver can be compiled with
`python scripts/build_catalog.py --out /tmp/candidate.duckdb`; only promote a candidate
if its data gates pass. Missing scopes must not be treated as zero successful comparisons.

## Offline real-browser acceptance

Install dependencies, then:

```bash
python -m playwright install chromium
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8101 npm --prefix web run build
python scripts/browser_smoke.py
```

The smoke starts a temporary fixture DuckDB, actual FastAPI and built Next.js servers
on 8101/3101. It uses the production runner with injected search/fetch providers and no
model credentials. Browser traffic outside localhost is blocked. It exercises live
POST/SSE, published 2→4→5, table/chart display, source citations, web/URL success,
controlled provider failure with prior table retention, and generic date filtering.
No replay stream is used. Servers and temporary data are cleaned up. CI runs this job.

For the normal local app, rebuild without the smoke API URL (use port 8000), or run
`NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm --prefix web run dev`.

Remaining limits: process-local state, bounded generic questions/date syntax, optional
external service reliability. The browser smoke uses a synthetic regression catalog and
cannot prove live gold completeness or external source correctness.

## Local acceptance report — 2026-09-22

Verdict: **READY FOR DEMO** for the available published/generic series. This does not
certify completeness of the full BDDK archive or a successful rebuild of the entire lake.

### Files changed

- `.github/workflows/ci.yml`: offline browser job.
- `contracts/sse-event.schema.json`: informational result extension.
- `src/kkb_agent/api/contracts.py`: exclusive frame/informational result and safe citations.
- `src/kkb_agent/api/turn1_runner.py`: successful informational events, injected provider
  boundaries, initial chart selection, coherent independent frame identities, visible fallback.
- `src/kkb_agent/api/main.py`: usable catalog required for core health; LanceDB optional.
- `src/kkb_agent/agent/analyze.py`: explicit windows and source-only generic plan validation.
- `src/kkb_agent/agent/date_window.py`: bounded date parser.
- `src/kkb_agent/agent/router.py`: safe failure messages and date refusal handling.
- `web/src/lib/ask.ts`: extended result type.
- `web/src/lib/analysis-frame.ts`: optional source-lineage type.
- `web/src/components/ask-panel.tsx`: informational citations, honest completion state,
  continuation from successful frames, testable outcome/version attributes.
- `web/src/components/analysis-table.tsx`: per-column source references.
- `tests/kkb_agent/api/test_informational.py`: provider, window, identity, failure,
  health and source-only plan regression tests.
- `tests/kkb_agent/api/test_main.py`: optional health and populated core readiness expectations.
- `tests/kkb_agent/api/test_turn1_runner.py`: web success expectations.
- `scripts/browser_smoke.py`: actual browser/frontend/backend test with provider fixtures.
- `docs/end-to-end-closure.md`: semantics, commands, limitations and this report.

No frozen AnalysisFrame/Operation changes. No golden fixture changes. Generated
Next.js agent files and next-env changes were removed from the patch. No credentials,
local gold, caches or Docker artifacts are included. No commit/push performed.

### Automated verification

- Full backend: **889 passed, 7 skipped, 1 warning** (Starlette/AnyIO deprecation).
- Seven skips are explicitly gated live external URL/browser tests.
- Frontend typecheck: PASS.
- Production Next.js build: PASS.
- Real browser smoke: PASS, including published 2→4→5, HTTP/SSE, visible tables/charts,
  web/URL citations, provider failure, retained prior frame, generic requested window,
  and supported causality result or controlled not-identifiable evidence.
- Fixed-snapshot regression: PASS; expected output unchanged.
- Ruff: PASS. Format: 157 files already formatted. `git diff --check`: PASS.
- No separate frontend unit-test command is defined in package.json; browser smoke covers
  the live UI. CI job was added but remote CI has not been run in this session.

### Live local smoke

Configured MIA was allowed through the existing client; no credential was printed.
All seven attempted query paths returned successful completion:

| Path | Evidence | Observed elapsed seconds |
| --- | --- | ---: |
| Turn 1 | version 2, nominal + rate, 60 rows | 40.81 |
| Turn 2 | version 4, CPI + derived real, 60 rows | 0.01 |
| Turn 3 | version 5, HPI, 60 rows | 0.01 |
| Generic window | 2022–2024, 36 rows | 28.90 |
| Anomaly | 66 source observations | 15.30 |
| Web search | informational success, 5 source entries | 15.91 |
| URL reading | informational success, 1 source entry | 3.85 |

The first generic live run revealed an unnecessary model-emitted rebasing operation.
The bounded generic path now accepts only the resolved source-column additions and
falls back deterministically for other plans. A regression test covers it. After restart,
the same live query returned **one source column, 36 rows, version 1**, matching result
and frame identities and successful completion. Its elapsed time was not separately recorded.
These are observed timings, not a latency guarantee; the first model call can exceed the
outer planning retry budget.

Frontend `http://127.0.0.1:3000`, backend `/health` on port 8000, and SearxNG on port
8888 all returned HTTP 200. The local servers/container were left running for testing.

### Data preparation result and remaining limitations

Existing gold has 291 catalog rows and 24,377 observations. Required demo observations:
nominal/CPI/HPI each 60 monthly rows in 2021–2025; rate has 261 weekly observations.
All four have zero missing raw values in that window.

The available bronze contains only the monthly BDDK table-4 sector slice, plus EVDS
silver (250 series). A candidate build at `/tmp/kkb-closure-gold.duckdb` completed its
parsing but returned failure at the cumulative/sign and bank-group partition gates:
there are no eligible full-scope comparisons. That candidate was **not promoted** to
`data/gold/lakehouse.duckdb`. The existing gold stayed in use. Full monthly scopes,
weekly BDDK and FinTürk acquisition are not present locally. Do not describe this as a
full production snapshot rebuild PASS.

Other real limitations: in-memory state is lost at restart; general dates/questions are
bounded; no vector retrieval is activated; final prose remains deterministic; external
service availability and model latency vary. Existing clients that dereference frame
unconditionally need the new informational result handling before using web/URL success.
