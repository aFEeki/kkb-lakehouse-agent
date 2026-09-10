# KKB Hackathon 2026 — Plan

Window closes **Sun 20 Sep**. Today is Thu 10 Sep.

## What we're building

Ask a question in Turkish about the Turkish economy → the system finds the data, plans the
analysis, picks its own tools, checks the result, and shows where every number came from.

Data: BDDK bulletins + EVDS, Jan 2021 – Jun 2026.

## The rule when we run out of time

**Data correctness > coverage > agent cleverness.** Cut from the bottom.

A wrong number that looks right is the worst outcome available to us. A narrow system that
is right about what it does beats a broad one that isn't.

## The thing they will actually test

The organizers published a 3-turn conversation:

1. Show housing loans + interest rates monthly, 2021–2025. Did volume rise when rates fell?
2. "Without breaking the table" — deflate only the loan amounts for inflation.
3. "Without breaking this table at all" — add house price index as a column.

"Without breaking the table" twice in three turns. **One living table that gets modified in
place.** Rebuilding it each turn = fail, and it fails invisibly (chart still renders, numbers
are just wrong).

So: the analysis result is a persistent object with a **fixed date spine**. Every column add
is a left join onto that spine with row count asserted unchanged. The model emits operations
against the object; it never regenerates it and never does arithmetic.

## Stack

Confirmed from the MIA guide — OpenAI-compatible at `https://mia.csp.kloudeks.com/v1`:

| Model | For | Limit |
|---|---|---|
| `kkbhackathon2026/Qwen3.8-27B` | planning, narrative, vision | 5 images/prompt |
| `kkbhackathon2026/Qwen3-Embedding-8B` | semantic search over series | — |
| `kkbhackathon2026/Unlimited-OCR` | PDF/image text extraction | 3 images/prompt |

**Not documented: tool calling.** Test it in the first hour of Day 0. Also test whether
`extra_body={"guided_json": ...}` works — the OCR example passes `vllm_xargs` through, so the
server is vLLM and guided decoding may be available. If either works, the planner is easy.
If neither does, we design around constrained prompting + validation.

API key: env var only, `.env` gitignored, **all MIA calls server-side** (never from the browser).

## The 10 days

**D0 Thu 10** — Test tool calling. Download every BDDK/EVDS source file (raw, no parsing).
Repo + `.gitignore` (`.claude/`, `.env`). Post remaining questions to Slack.

**D1 Fri 11** — Benchmark MIA: structured-output success rate, latency, context, rate limit,
Turkish quality. Freeze the analysis-object contract — everything else builds on it. EVDS
ingestion working.

**D2 Sat 12** — Parse BDDK Excel → clean tables. Multi-row headers, `1.234,56` numbers,
sheet names that drift. Biggest time sink in the project. Build the series catalog.

**D3 Sun 13 — GATE** — De-cumulation. See below. Nothing ships past here until tests pass.

**D4 Mon 14** — Turn 1 working end to end in a browser.

**D5 Tue 15 — GATE** — Turns 2 and 3. Column-scoped deflation, join without breaking rows,
undo. Provenance drill-down in the UI. This is the day that decides whether we place.

**D6 Wed 16** — Anomaly, change detection, causality.

**D7 Thu 17** — URL agent: static HTML, JS-rendered (headless browser), PDF, Excel, image.
Two-hop link discovery. Manual upload fallback. Hard timeouts.

**D8 Fri 18** — UI shows the 5 stages as they run. **Deploy live** — two days early on purpose.
Freeze the data snapshot.

**D9 Sat 19** — Eval suite (~25 Turkish questions, run as regression). Break it deliberately:
bad URLs, missing series, nonsense questions. Write the docs KKB asked for.

**D10 Sun 20** — Feature freeze at midday. Rehearse the demo 5× on the deployed system, timed.
Grant KKB repo access. Verify no `.claude/` in history.

## The one thing that can kill us

Some BDDK data is cumulative. Some resets every January. Some never resets. **On a chart they
look identical.**

The obvious test — "does the line only go up?" — **does not work here.** Turkish lira figures
rose almost every month regardless, inflation peaked near 85%. A rising line proves nothing.

What actually discriminates: **a drop each January.** Classify each series individually, record
the evidence, have a human confirm it. Automated checks that block the build:

- YTD series: de-cumulated months of a year must sum to that year's December total
- De-cumulated flows can't go negative
- No >100× jump between periods (catches Bin/Milyon/Milyar unit switches mid-series)
- Where BDDK and EVDS publish the same aggregate, they must agree
- Row count unchanged after every join
- NaN never silently filled

Also per series: is it a stock or a flow, and does it sum or take end-of-period when converting
weekly → monthly? Wrong aggregation rule is as damaging as wrong cumulative mode.

Watch out: Q1 asks for *kullandırılan* housing loans (new loans extended, a flow). The series
most people grab is the housing loan *stock*. Different numbers. Same trap with deposit banks
vs all banks, TRY vs FX-adjusted, seasonally adjusted or not.

## Must be true on 20 Sep

- [ ] 3-turn conversation runs live; turns 2–3 modify one table, don't rebuild it
- [ ] Every number traceable on screen to source + transformation chain
- [ ] An unseen URL handed to us at runtime produces a usable number
- [ ] All six tools work and can be seen being chosen
- [ ] Deployed, reachable from outside, repo ready for KKB

## Six tools (all required)

Lakehouse (query/join our pool) · Web Search · Web URL Agent (PDF/Excel/image/text) ·
Anomaly · Causality · Change Detection

On causality: with 60 monthly points of non-stationary Turkish data, "this is correlation, not
causation, here's the confound" is often the correct answer. Build the refusal path — it scores
better than a p-value, and overclaiming is the fastest way for this jury to dismiss us.

## Cut list (in order, if we fall behind)

1. FinTürk province data
2. BDDK weekly bulletins (monthly only)
3. Full-catalog semantic search → curated whitelist + keyword search
4. Formal causality tests → correlation + lead/lag + the refusal language
5. Frontend polish
6. Live-fetch for out-of-scope series → "not in our pool"

**Never cut:** the table-mutation semantics, de-cumulation correctness, visible provenance,
the three published turns, live deployment.

## Still unknown — ask in `02-soru-cevap`

- Where does "deployed live" run — Kloudeks or our own infra?
- Does the third-party-LLM ban cover commercial search APIs? (defaulting to self-hosted
  SearxNG so the answer can't invalidate work)
- Context window and rate limits for Qwen3.8-27B
- Any official BDDK metadata on which series are cumulative?
- Internet access at the venue on demo day?

## Repo rules

- Never commit `.claude/`, `CLAUDE.local.md`, local settings, or `.env`
- No assistant attribution in commits, PR titles, or descriptions — history reads as ours
- Check staged files and the message against both before every commit
