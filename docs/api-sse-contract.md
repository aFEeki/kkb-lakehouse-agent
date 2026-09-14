# API and SSE contract v1

This document defines the transport shapes and the backend SSE boundary. Runtime
orchestration and the frontend `EventSource` client remain outside this scope.

## Sources and committed artifacts

The immutable Pydantic contracts live in `kkb_agent.api.contracts`. Their generated,
language-neutral forms are committed as `contracts/ask-request.schema.json` and
`contracts/sse-event.schema.json`. A drift test requires those files to equal the schemas
generated from the Python contracts.

Frontend development can replay `tests/fixtures/api/successful-stream.jsonl` and
`tests/fixtures/api/error-stream.jsonl`. Each line is the JSON object carried in one future
SSE `data:` field. `POST /ask` now uses the envelope's `type` for the SSE `event:` field and
`event_id` for `id:`. Each event ends with a blank line and is independently parseable.

## HTTP execution boundary

`POST /ask` validates its JSON body directly as the frozen `AskRequest` and returns
`text/event-stream` with `Cache-Control: no-cache`, `Connection: keep-alive`, and
`X-Accel-Buffering: no`. It consumes the injected asynchronous `AskRunner`; the route does
not resolve series, call a model, execute operations, or generate narrative itself.

Until production orchestration supplies an `AskRunner`, the default runner produces a safe
failed stream. Runner exceptions are logged server-side and mapped to the existing typed
`error` contract without exception text, traceback, environment values, or credentials.
An active stage is then closed as failed and the stream terminates with
`completion(outcome="failed")`.

## Ask request

An ask request has exactly three fields:

- `analysis_id` identifies one logical analysis and must remain unchanged throughout its
  event stream.
- `version` is the exact non-negative `AnalysisFrame.version` last observed by the client.
  It is an optimistic-concurrency precondition, not a minimum version. A future runtime must
  reject a stale or future version rather than silently rebasing the request.
- `question` is the non-empty user request.

Unknown fields and implicit coercion of `analysis_id` or `version` are rejected.

## Event envelope

Every event carries `event_id`, `analysis_id`, zero-based `sequence`, non-negative
`frame_version`, timezone-aware `occurred_at`, the discriminating `type`, and its typed
`payload`.

The payload responsibilities are:

| Event type | Payload responsibility |
|---|---|
| `stage_start` | Names the five-stage pipeline stage that began. |
| `stage_end` | Names the matching stage and records `succeeded` or `failed`. |
| `tool_selected` | Names the deterministic tool selected within the active stage. |
| `result` | Carries the validated `AnalysisFrame` and evidence-bounded answer. |
| `error` | Carries a stable public code, one-line user-safe message and retryability flag. |
| `completion` | Terminates the stream with `succeeded` or `failed`. |

The error payload deliberately has no exception, detail, traceback or stack field. Extra
fields are forbidden, and traceback-like or multiline `user_message` values are rejected.
Internal exceptions must be logged outside this public contract and mapped to stable codes.

## Result snapshot decision

The `result` event embeds a complete validated `AnalysisFrame`. This reuses the existing
domain contract instead of redefining columns, units, findings, charts or recursive
lineage/provenance in an API model. It gives the table, chart and provenance views one
self-contained fixture without assuming a persistence service or snapshot retrieval route
that does not exist yet.

This choice may be revisited if frames become too large for a single event. Moving to a
snapshot reference would require a durable snapshot store and retrieval contract and is
therefore outside v1.

## Ordering and version rules

For one response stream:

1. Every event has the request's `analysis_id`; `event_id` values are unique.
2. `sequence` starts at zero and increases by exactly one with no gaps.
3. `occurred_at` is non-decreasing.
4. The first `frame_version` equals the ask request version. Later versions never decrease.
5. Stages do not overlap. Each `stage_start` has one matching `stage_end`; tool selection
   occurs while a stage is active.
6. A `result` event is outside an active stage. Its embedded frame ID equals `analysis_id`,
   and its embedded frame version equals the envelope's `frame_version`.
7. Exactly one `completion` event appears and it is last. Its frame version is the final
   stream version.
8. Successful completion has a preceding result and no error. Failed completion has a
   preceding error and no result after that error.

The fixture tests enforce these rules and validate every JSONL event against the committed
contract.

## Agreement gate

Frontend and backend owners must review and accept this contract before either track treats
it as stable. Repository validation can prove schema and fixture consistency; it cannot
prove team agreement. That acceptance criterion remains external until the team records its
approval.
