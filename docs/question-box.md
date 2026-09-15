# Question box and streaming states

`AskPanel` is where a question is typed and where the answer arrives. It consumes
`POST /ask` (SCRUM-68/69) and renders each event as it lands.

## POST, so not EventSource

The browser's `EventSource` only issues GET requests, and `/ask` is a POST carrying
`analysis_id`, `version` and `question`. `askStream` therefore reads `response.body`
directly and parses the SSE framing itself: frames are separated by a blank line, only
`data:` lines are read, and a partial frame split across two chunks stays buffered until it
is whole. `id:` and `event:` are ignored deliberately — both duplicate fields that are
already inside the JSON, and one source of truth is better than three.

## Something is always moving

The backlog is explicit that a frozen screen reads as a crash. The panel never shows a bare
spinner. While a turn runs it shows:

- the five published stages in order, each either pending, running, succeeded or failed;
- the tool a stage selected, named in Turkish (`lakehouse` → "Lakehouse", `web_url` → "URL
  okuma");
- a per-stage elapsed time, and a line saying which stage is running right now.

The elapsed counters tick from a 100 ms interval that only runs while a turn is in flight.
That movement is tied to real state rather than decoration: if a stage genuinely hangs, the
screen says which stage and for how long, which is the thing an operator actually needs.

## Turns continue one analysis

Each turn sends the `frame_id` and `version` of the previous turn's result. That is what
"bu tabloyu bozmadan" asks of turns 2 and 3 — the request is a precondition on the exact
version the client last saw, so a stale or concurrent change is rejected by the backend
rather than silently rebased. The first turn opens a new analysis.

The conversation accumulates: every turn keeps its question, its stage timings, its answer
and its table, so the turn-1 table is still on screen when turn 2 is asked.

## Errors

An `error` event renders its `user_message` as written, with the stable `code` and whether
it is worth retrying underneath. Nothing else is shown — the contract already guarantees the
message is a single public line with no traceback in it. A transport failure (backend down,
connection dropped) is turned into the same shape locally with code `CONNECTION_FAILED`, so
there is one error presentation rather than two.

Verified against the live backend: a stale version renders as

> İstenen analiz sürümü mevcut veya güncel değil.
> ANALYSIS_VERSION_NOT_FOUND · tekrar denemeyin

## Known rough edge

Numbers come through as raw floats, so the table shows `18.387999999999998` where it should
show `18,39`. The values are correct; the presentation is not. Formatting belongs to the
table component (SCRUM-73) rather than here, and is worth fixing before the demo — a judge
reading a screen full of float artifacts will not assume they are a rendering choice.
