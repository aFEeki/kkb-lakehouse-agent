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

## Production `index_column` handler

The production composition point `create_operation_executor()` registers the completed
`OperationType.INDEX_COLUMN`, `OperationType.DEFLATE_COLUMN` and
`OperationType.REVERT_TO` behavior. All are deterministic and have no database, network or
model dependency.

For source value `value` and the value at the exact requested base date `base_value`, it
calculates:

```text
indexed_value = value / base_value * 100
```

The output key is `{source_key}__index_{YYYY-MM-DD}`. The label is
`{source label} (Index, YYYY-MM-DD = 100)`. Both are deterministic. An existing output key
is an error; the handler never overwrites or invents a numbered suffix.

Only a `date` spine is accepted. `IndexColumnParameters` carries a calendar date and cannot
identify one exact row on a datetime spine without an additional timestamp contract. The
handler therefore rejects datetime spines instead of matching by date or choosing a nearby
timestamp. It never sorts, resamples, interpolates or selects a neighbor.

The exact base date must exist, and its source value must be present, finite and nonzero.
Other missing values remain `None`. Every calculated value must be finite. Numeric strings
and booleans are not accepted as numbers.

The source column and its position are retained unchanged; the result is appended as a
`dtype="number"`, `origin="derived"`, `measure_type="index"` column with a dimensionless
index unit. Its recursive parent lineage captures the current frame ID/version, source key
and exact source lineage. Its `index_column` transformation records implementation version
`1`, the base date and fixed base value `100`.

The handler returns an uncommitted candidate: frame ID, version, spine, existing columns,
findings, charts and operation history are preserved. `OperationExecutor` subsequently owns
the one log append and one version increment.

## Production `deflate_column` handler

The handler accepts only the `cpi_base_period_constant_prices_v1` convention settled in
DECISIONS.md #9. For a nominal monetary stock and headline-CPI price-level column it uses:

```text
real_t = nominal_t * CPI_base / CPI_t
```

The result is expressed in the target's currency and scale at base-date prices; it is not
rebased to 100. The output key is
`{target_key}__deflated_by_{deflator_key}_{YYYY-MM-DD}` and its label is
`{target label} (real, {deflator label}, YYYY-MM-DD prices)`.

The target must be numeric with `measure_type="stock"`; the deflator must be numeric with
`measure_type="index"`. CPI levels must be positive and finite. The exact base date and its
CPI observation must exist. Missing target or CPI observations outside the base row produce
`None`; the handler never fills, interpolates, resamples or selects another period. As with
`index_column`, a datetime spine is rejected because the date parameter cannot identify an
exact timestamp.

The original target, deflator and all other columns retain their order and values. The real
column is appended with the target's unit and scale. Its recursive lineage records both the
nominal and CPI parents, plus the convention, base date, deflator key and formula. Version
and operation history remain unchanged until the executor commits the candidate.

## Deferred operation behavior

No production handler is registered by default. The operation-specific backlog tasks
still own:

- catalog fetch and left-join behavior for `add_column`;

Persistence, conversation state, planner/MIA integration and numerical postconditions
remain outside this foundation.

## Snapshot retention and `revert_to`

Each production executor owns an explicit in-memory `FrameSnapshotHistory`; callers may
inject one into `create_operation_executor(snapshot_history=...)`. It has no global state,
service locator or hidden persistence. After every successful transition, the executor
atomically retains immutable analytical snapshots for both the source and successor.
Operations are never replayed to reconstruct an old version.

A retained snapshot contains frame ID, version, spine, columns, charts and the operation-log
prefix that proves its place in the current history. Findings are audit evidence rather than
restorable analytical state and remain on the live frame. This also allows findings to be
appended at an existing analytical version without creating conflicting snapshots.

For `revert_to(target_version)`, the executor verifies that the target is retained under the
same frame ID and that its operation prefix matches the current history. It restores the
target's columns, charts and identical spine, preserves all current findings, then appends
the revert operation once and advances to `current_version + 1`. It never resets the frame
version or truncates operations. A finding created after the target remains visible with its
original `frame_version`, even if its supporting column is absent from the restored current
analytical state.

Revert construction and history retention validate fully before committing either result.
Missing, conflicting or unrelated snapshots return no frame and retain no failed transition.
