# Operation executor foundation

`kkb_agent.agent.OperationExecutor` is the deterministic boundary between a validated
planner operation and future operation-specific business logic. It contains no LLM,
database, network or service-locator dependency.

## Validation and dispatch

The executor accepts an existing `AnalysisFrame` and an already validated `Operation`.
Serialized planner output must first pass `Operation.model_validate(...)`; the executor
does not maintain a second request schema. Invalid vocabulary or parameter payloads are
therefore Pydantic validation failures. Passing an unvalidated mapping to `execute` raises
`UnsupportedOperationError` with the required validation path.

Handlers are supplied explicitly as a mapping from `OperationType` to a callable. The
mapping is copied into a read-only registry. String keys, dynamic imports, arbitrary
function names, Python, SQL and expression fallbacks are rejected. A valid enum member
without a registered handler raises `UnimplementedOperationError`; this distinguishes
unfinished business behavior from an invalid operation payload.

The registry is intentionally supplied to the constructor. Production composition can
register handlers as their separate backlog tasks are completed, while tests can prove
the lifecycle with a small local handler. No global mutable registry or plugin discovery
is introduced.

## Success lifecycle

Execution proceeds as follows:

1. Verify input types and require the operation source/result versions to match the
   current frame and its next version.
2. Dispatch to the explicitly registered handler.
3. Require the handler to return a structurally valid `AnalysisFrame` candidate with the
   same frame ID, version, operation history and spine.
4. Construct a new successor snapshot by appending the requested operation exactly once
   and advancing the version exactly once.
5. Call `candidate.assert_successor_of(previous)` and enforce the executor-specific exact
   operation-log postcondition.

Handlers own future changes to columns, findings and charts. The executor alone owns the
operation-log append and version transition. A handler must not commit those itself.

## Failure atomicity

Frame contracts are frozen and the executor never mutates its input. Validation,
unregistered operation, handler, candidate or successor failures return no snapshot;
the original frame retains its version and operation history. Handler exceptions are
wrapped in `OperationHandlerError` with their original exception as the cause. Structural
failures become `OperationPostconditionError`. Version mismatches use
`OperationVersionError`. Messages identify the operation and frame version.

Spine length, ordering, values, key and kind are checked through the existing frame
invariants. The executor does not sort, truncate, pad or repair a handler result.

## Deferred operation behavior

No production handler is registered by default. The operation-specific backlog tasks
still own:

- catalog fetch and left-join behavior for `add_column`;
- deflator selection and constant-price arithmetic for `deflate_column`;
- rebase-to-100 arithmetic and base-value checks for `index_column`;
- historical snapshot restoration for `revert_to`.

Persistence, conversation state, planner/MIA integration and numerical postconditions
remain outside this foundation.
