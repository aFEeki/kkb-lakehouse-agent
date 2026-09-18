# Demo hardening

Four changes found by driving the served system from a browser rather than from a test.
Each is small; together they are the difference between a demo that reads as working and
one that reads as broken.

## A question typed without Turkish diacritics is still the same question

`classify_published_turn` matched the accented spelling only, so "enflasyondan arindirir
misin" was refused while "enflasyondan arındırır mısın" was accepted. The organizers' own
slide writes "yeni bir **sutun** olarak" without the u-umlaut, which means the published
question as published did not match.

`_fold` now casefolds through `turkish_casefold` and then strips diacritics, and both the
question and the search terms go through it. Typed in caps, in ASCII, or with full
diacritics, the same three turns are recognised.

## The tool is named before it runs, not after

`_route` ran the whole tool and then emitted `stage_start`, `tool_selected` and `stage_end`
together. The screen therefore stayed empty for as long as the work took, and the stage
appeared already finished with a duration of 0.0 s — the two things that make a working
system look hung.

Tool selection is now a separate step: select, announce, then run. `run_tool` takes an
optional `choice` so the caller that already selected can pass it in rather than selecting
twice. The stage now shows its real duration while it is running.

## A tool that ran is not a failure

The ask contract requires an `AnalysisFrame` in `ResultPayload`, and a tool result does not
have one, so the backend has to deliver it on the error channel. The frontend was then
painting it in the failure colours: the anomaly tool finding five outliers in sixty-six
observations was presented to the reader as an error.

`TOOL_RUN_NOT_RENDERABLE` is now rendered as ordinary answer text with a quiet line saying
the result cannot be shown as a table yet. Nothing about the message is changed — it is the
backend's own wording — only the claim the styling was making about it.

The proper fix is still open: build a frame from the series the tool read, so the result
goes out as a `result` event with the series as a column.

## Planning is bounded in wall-clock time

MIA writes roughly eight tokens a second. A sampled plan of a few hundred tokens therefore
costs most of a minute, and `PLAN_ATTEMPTS = 3` means a bad first sample can triple it; one
measured run spent 54 s inside a single call.

`PLAN_BUDGET_SECONDS = 25.0` caps the attempts as a whole. The first attempt always runs —
cutting the model out entirely would make the served system less agentic, which is the
thing the hackathon is judged on — but a retry only starts if the budget has not already
gone. The scripted plan produces the same table, and the answer already says which plan it
used, so a fallback is disclosed rather than hidden.

Measured after the change, three consecutive runs of the published turn 1 took 5.1 s, 13.1 s
and 18.2 s, all of them planned by the model rather than the script.

## Still open

Tool results have no frame, so they cannot carry a table. Table values render as raw floats
(`18.387999999999998`). Questions outside the three published turns and the router's tools
are still refused.
