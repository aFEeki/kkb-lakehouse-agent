# Decisions

Open decisions for the sprint. Fill in **Decision** and **Owner** as they're settled; leave
the reasoning in [PLAN.md](PLAN.md) rather than restating it here.

| # | Decision | Deadline | Status |
|---|---|---|---|
| 1 | Time budget per person | Today | **dropped** — no fixed budget |
| 2 | Who owns the data layer | Today | **contested** — see below |
| 3 | BDDK scope | Today | **settled** — monthly only |
| 4 | Size of the data pool | Today | **settled** — ~250, chosen backwards from a question list |
| 5 | Frontend framework | Today | **settled** — React / Next.js |
| 16 | Neo4j | Today | **contested** — recommend skipping |
| 17 | AI in the data layer | Today | **settled** — build-time yes, runtime no |
| 6 | How much the model may do | Day 1 | open |
| 7 | Conversation state shape | Day 1 | open |
| 8 | Output language | Day 1 | open |
| 9 | Deflation convention | Day 3 | open |
| 10 | Which "housing loan" | Day 3 | **decide before ingestion** |
| 11 | Ragged edge policy | Day 3 | open |
| 12 | Snapshot or live | Day 3 | open |
| 13 | Behavior on a miss | Day 5 | open |
| 14 | Refusal posture | Day 5 | open |
| 15 | Search backend | Day 5 | open |

---

## Settled

### 1. Time budget per person — dropped

No fixed hours. Everyone contributes when they can.

One ask: know roughly who is around on **Sat 12 and Sun 13 Sep**. The BDDK parsing and the
de-cumulation gate both land that weekend, and those two days carry more risk than the rest
of the week combined.

### 3. BDDK scope — monthly bulletins only

Weekly bulletins hold most of the parsing volume and the published demo scenario needs none
of them. Add weekly only if Day 4 arrives on schedule. FinTürk province data is first on the
cut list.

### 4. Size of the data pool — ~250 series

Chosen by working backwards, not by browsing EVDS: write down the published three-turn
scenario plus the ten most likely adjacent questions a judge might ask, then pick the series
that cover them.

Coverage: credit by type (stock *and* flow), rates by loan and deposit type, TÜFE/ÜFE, house
prices and residential sales, FX and reserves, deposits including KKM, banking aggregates
(NPL, CAR, sector balance sheet), real-economy basics (industrial production, capacity
utilisation, unemployment).

This lands around 240–250 and gives a defensible answer to "why these?" — a question we will
be asked.

- **Owner:**

### 5. Frontend — React / Next.js

The API key never goes near it. Browser → FastAPI → MIA. No exceptions.

- **Owner:**

### 17. AI in the data layer — build-time yes, runtime no

**Use AI heavily to build the pipeline.** Writing BDDK parsers, populating catalog rows,
reading Turkish series names and footnotes to propose `measure_type` and `cumulative_mode`.
This is a large accelerator and is how ten days becomes feasible.

**Do not put AI inside the data path at runtime.** Every transform that touches a number is
deterministic Python, tested and replayable.

The reason is structural, not stylistic: the trust layer requires that every figure trace
back to a transformation chain. If an LLM improvised the transformation, there is no chain —
only an assertion that something happened. It also can't be unit-tested, can't be reproduced,
and adds latency to every query. The one thing we are graded on hardest is the thing an LLM
in the data path would quietly destroy.

**The working pattern:** LLM proposes → deterministic tests verify → human adjudicates only
what the tests can't settle.

---

## Contested — needs a call

### 2. Who owns the data layer

Current position: everyone splits it, no owner.

**Parsing splits fine.** Files are naturally parallel — one person takes 2021 monthlies,
another takes 2022, another builds catalog rows. Four people genuinely go faster here.

**Classification does not split.** Deciding which series are cumulative is a judgment call
made repeatedly, and it has to be made the same way every time. Four people doing it
independently produce four standards for what counts as a January reset, and a gold layer
nobody can vouch for.

- **Recommend:** parsing stays distributed; one person owns the classification standard and
  signs off on the gold build. Not a week of work — the deterministic tests resolve most
  series automatically, leaving perhaps 10–20 genuinely ambiguous ones. Closer to an hour of
  concentrated judgement than a full track.
- **Decision:**
- **Owner:**

### 16. Neo4j

Proposed as an addition. **Recommend against**, at least until after Day 5.

The test that matters: *what query do you need that DuckDB can't answer?*

Lineage is genuinely graph-shaped, so the instinct isn't wrong — but the graph is tiny. A few
dozen nodes per analysis object, tracing columns through transforms back to sources. That's a
Python object tree serialised to JSON in a DuckDB column. A graph database for forty nodes
buys a talking point and costs a service to deploy, operate and debug during the tightest
week of the project.

The organizers named DuckDB, LanceDB, Pandas, pypdf and Plotly. Neo4j isn't forbidden, but
adding an unlisted heavyweight service when a listed one covers the need is a weak trade in
front of judges who wrote that list. It's also one more thing that can be down on demo day.

- **Would change my mind if:** someone already knows Neo4j well, wants to own it, and it goes
  in *after* Day 5 as an addition to a working system — never as a dependency the trust layer
  is built on.
- **Decision:**
- **Owner:**

---

## Day 1 — architectural, expensive to reverse

### 6. How much the model may do

Closed operation vocabulary only, or an escape hatch where it writes pandas for novel
requests?

- **Recommend:** closed vocabulary, with a guarded escape hatch that must pass the same
  spine assertions. Decide *now* what validates the hatch, or it becomes a hole.
- **Decision:**
- **Owner:**

### 7. Conversation state shape

One analysis object per conversation or several named ones? Forkable? How does "this table"
resolve?

- **Recommend:** one active object plus history, no forking. The demo needs no more, and
  more is surface area for bugs.
- **Decision:**
- **Owner:**

### 8. Output language

- **Options:** Turkish only · Turkish + English
- **Recommend:** Turkish only. The jury is Turkish and split effort shows.
- **Decision:**
- **Owner:**

---

## Day 3 — data conventions we have to disclose

Not optional details. Each one ends up printed in a column label or defended to a judge.
These are easier to settle with data in front of you than in the abstract — with one
exception, #10, which gates ingestion.

### 9. Deflation convention

Which index (TÜFE headline, ÜFE, housing-specific deflator), which base period, and whether
"reel" means constant prices or index-normalised. Turn 2 of the demo depends on this being
fixed and stated.

- **Decision:**
- **Owner:**

### 10. Which "housing loan" we mean — decide before ingestion

The demo says *kullandırılan* — a flow of new extensions. The series most people grab is the
stock. Different numbers, different story, and the demo question is ambiguous between them.
Also: deposit banks / all banks / incl. participation; TRY or FX-adjusted; seasonally
adjusted or not.

- **Recommend:** pull both flow and stock, label each clearly in the catalog, let the agent
  choose and state which it used.
- **Decision:**
- **Owner:**

### 11. Ragged edge policy

Series end on different dates near June 2026 because publication lags differ.

- **Options:** truncate to common end · show gaps · nowcast
- **Recommend:** truncate with a visible notice. Never interpolate silently.
- **Decision:**
- **Owner:**

### 12. Snapshot or live

Both sources revise history.

- **Recommend:** pin a dated snapshot, show the date in the UI, add a refresh command.
  Reproducibility beats freshness here.
- **Decision:**
- **Owner:**

---

## Day 5 — product behavior

### 13. Behavior on a miss

Question asks for a series we don't have. Live-fetch at query time is a strong demo moment
when it works and a hang when it doesn't.

- **Recommend:** whichever way you go, put a hard timeout on it.
- **Decision:**
- **Owner:**

### 14. Refusal posture

How readily does the system say "I can't establish that from this data" — especially on
causality with 60 monthly points?

- **Recommend:** readily, and make the refusal well-argued. It's the highest-value thing
  the causality tool can do with this jury.
- **Decision:**
- **Owner:**

### 15. Search backend

Self-hosted SearxNG needs somewhere to run; a commercial API may or may not be permitted.

- **Recommend:** default to SearxNG so the pending compliance answer can't invalidate work
  already done.
- **Decision:**
- **Owner:**

---

## Not decisions — don't spend a meeting on these

Measurable against the MIA endpoint in about an hour: tool calling support, guided decoding,
context window, rate limits, embedding dimension, Turkish output quality. Measure them,
don't debate them. The only decision downstream is what to do if structured output proves
unreliable, and that's already in PLAN.md.

Where we deploy is partly KKB's answer to give. The part we control is the fallback:
provision our own VM regardless — an idle VM is cheaper than an undeployed submission.
