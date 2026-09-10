# Decisions

Open decisions for the sprint. Fill in **Decision** and **Owner** as they're settled; leave
the reasoning in [PLAN.md](PLAN.md) rather than restating it here.

| # | Decision | Deadline | Status |
|---|---|---|---|
| 1 | Time budget per person | Today | open |
| 2 | Who owns the data layer | Today | open |
| 3 | BDDK scope | Today | open |
| 4 | Size of the data pool | Today | open |
| 5 | Frontend framework | Today | open |
| 6 | How much the model may do | Day 1 | open |
| 7 | Conversation state shape | Day 1 | open |
| 8 | Output language | Day 1 | open |
| 9 | Deflation convention | Day 3 | open |
| 10 | Which "housing loan" | Day 3 | open |
| 11 | Ragged edge policy | Day 3 | open |
| 12 | Snapshot or live | Day 3 | open |
| 13 | Behavior on a miss | Day 5 | open |
| 14 | Refusal posture | Day 5 | open |
| 15 | Search backend | Day 5 | open |

---

## Today — these gate everything else

### 1. Time budget per person

Full-time for ten days, or evenings around jobs and classes? Four people at 3 h/day is
~120 person-hours; four full-time is ~400. Those are different projects, and every scope
decision below assumes an answer.

- **Decision:**
- **Owner:**

### 2. Who owns the data layer

~40% of the work and the only track that can silently produce a wrong number. Needs the
strongest person on messy data, not whoever is free. Other tracks: agent core, tools,
frontend + deploy.

- **Decision:**
- **Owner:**

### 3. BDDK scope

Biggest single time lever in the project. Weekly bulletins hold most of the parsing volume
and the published demo scenario needs none of them.

- **Options:** monthly only · + weekly · + FinTürk province data
- **Recommend:** monthly only for v1; add weekly if Day 4 arrives on schedule
- **Decision:**
- **Owner:**

### 4. Size of the data pool

"All EVDS series" is not a plan. Pick a number and a set of categories — credit by type,
rates, TÜFE/ÜFE, house prices and sales, FX, deposits, NPL, industrial production — and be
able to say why those.

- **Recommend:** 200–300 curated series as the fast path, full-catalog search as a
  discovery path on top
- **Decision:**
- **Owner:**

### 5. Frontend framework

Next.js is permitted but costs real time and needs someone who can move fast in it. What's
graded is the five stages being visible, not the framework.

- **Options:** Next.js · simpler Python-served UI
- **Recommend:** decide by who you actually have, not by what sounds better
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

### 9. Deflation convention

Which index (TÜFE headline, ÜFE, housing-specific deflator), which base period, and whether
"reel" means constant prices or index-normalised. Turn 2 of the demo depends on this being
fixed and stated.

- **Decision:**
- **Owner:**

### 10. Which "housing loan" we mean

The demo says *kullandırılan* — a flow of new extensions. The series most people grab is the
stock. Also: deposit banks / all banks / incl. participation; TRY or FX-adjusted;
seasonally adjusted or not.

- **Recommend:** pick explicitly and record it in the catalog. A domain judge will ask.
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

---

**If only three get settled in the next hour:** #1 time budget, #2 data owner, #3 BDDK
scope. Those determine whether the rest of the plan is real.
