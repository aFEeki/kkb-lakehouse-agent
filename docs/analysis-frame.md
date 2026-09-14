# AnalysisFrame contract (v1)

This contract defines a source-agnostic analysis snapshot. It does not run operations.
The fixed-spine requirement comes from README.md and PLAN.md; DECISIONS.md remains
authoritative for product choices. No existing decision is closed by this document.

## Public API and files

Import public types from `kkb_agent.frame`:

- `AnalysisFrame`, `Spine`, `Column`, `Unit`.
- `Lineage`, `SourceReference`, `ParentLineage`, `Transformation`, `MetadataEntry`.
- `Finding`, `SpineRange`, `ChartSpec`.
- `Operation`, `OperationType`, `AddColumnParameters`, `DeflateColumnParameters`,
  `IndexColumnParameters`,
  `RevertToParameters`.
- `assert_spine_intact`, `assert_existing_columns_intact`, `assert_columns_aligned`, `SpineViolation`,
  `ColumnAlignmentViolation`.

`models.py` defines snapshots and evidence. `operations.py` defines audit records and
closed parameter schemas. `invariants.py` contains standalone guards. `_base.py` is an
internal shared Pydantic base and scalar types; it prevents circular dependencies
between snapshot and operation models.

## Spine and columns

A `Spine` has a stable `key`, `kind` (`date` or `datetime`), optional display `label`,
and ordered `values`. Daily/monthly dates use `date`; timestamps must have a timezone
and are normalized to UTC. ISO date/time strings are accepted for serialization.
Numeric epochs, missing row identities, naive timestamps and duplicate identities
are rejected. Dates and timestamps cannot be mixed in one spine.

Input order is preserved, including explicitly supplied descending order. There is
no implicit sorting or frequency inference. Date spines use calendar dates, not
source columns literally named `date`. Non-temporal row identities are outside v1.

`assert_spine_intact(before, after)` compares key, kind, values and order. A label
change is presentation-only. Shortening, extending, reordering or replacing an
identity raises `SpineViolation`.

For `add_column`, `deflate_column` and `index_column`, the executor also calls
`assert_existing_columns_intact(before, after)`. Existing columns must remain the same
ordered prefix. Each column's UTF-8 encoded `model_dump_json()` is compared, so values,
labels, units, measure metadata, origin and recursive lineage/provenance must all remain
byte-identical. The invariant permits zero or more appended columns; operation-specific
handlers remain responsible for deciding what new columns they produce. `revert_to` is
excluded because restoring an earlier retained column snapshot is its intended behavior.

A `Column` has a stable `key` separate from its `label`, a `dtype`, ordered `values`,
optional `measure_type` and `unit`, `origin`, and mandatory `lineage`.

Supported dtypes: `integer`, `number`, `string`, `boolean`. No numeric-string parsing,
boolean-to-number conversion or float-to-integer truncation occurs. `number` accepts
integers and finite floats. Missing values are exclusively `None`/JSON `null`;
`missing_count` is derived from values. NaN and infinity are rejected rather than
silently converted to null or zero. Financial semantics remain unknown (`None`)
until supplied by the catalog. Unit metadata has an optional symbol/description and
an explicit positive scale; no unit conversion runs here.

Columns are stored as a tuple, preserving insertion order. Duplicate keys and any
column length different from the spine are rejected during frame construction.
`assert_columns_aligned(spine, columns)` also exposes this check independently with
`ColumnAlignmentViolation`. Construction wraps domain ValueErrors as Pydantic
ValidationErrors. An empty spine with no columns or zero-length columns is valid;
a nonempty spine with no columns is also a valid initial snapshot.

## Lineage

`SourceReference` records source type, exact opaque source reference, optional
retrieval timestamp, optional lowercase SHA-256, and extension metadata. Missing
retrieval/hash information remains explicitly null. This structural contract does
not certify provenance completeness; a future gold/publication gate must enforce
mandatory source evidence before publishing numbers.

`Lineage` contains ordered direct sources, parent snapshots and transformations.
At least a direct source or parent is required. Each `ParentLineage` identifies a
frame ID, exact frame version and column key, and embeds that parent's recursive
lineage. This preserves origin evidence even if an earlier column is replaced.
A source column requires direct sources without parents. A derived column requires
parents and a nonempty transformation chain.

A transformation has a name, implementation version and ordered metadata entries.
Extension/parameter metadata is a tuple of key/scalar-value entries, allowing
source-specific keys without mutable arbitrary dictionaries. Scalar values are
string, integer, finite float, boolean or null. Names/metadata are descriptive audit
data, never expressions to evaluate. Parent snapshot existence and numerical
reproducibility require a later repository/executor layer.

## Findings and charts

A `Finding` records a stable ID, statement, evidence `frame_version`, supporting
column keys, optional `SpineRange`, producing tool, status, optional confidence in
[0, 1], caveats and optional `supersedes` finding ID.

`SpineRange` is a nonempty half-open row interval `[start, stop)` in the evidence
version, rather than a date string interpreted against today's frame. Current-version
column references and range bounds are validated against the current snapshot.
Historical findings retain references to their original version; validating those
references requires that historical snapshot, and is deliberately deferred.

A revision is a NEW finding that references an earlier finding in the ordered list.
Earlier statements/evidence are retained. `kkb_agent.agent.create_finding(...)` appends a
current-version finding and `kkb_agent.agent.revise_finding(...)` appends a new finding whose
`supersedes` field identifies its direct predecessor. Repeated revisions target the newest
finding, producing an ordered chain; branching from a finding that already has a direct
revision is rejected. Stored status is the status recorded at creation. Consumers determine
effective supersession from later `supersedes` links, without editing historical records.

Both APIs reconstruct and validate a new immutable `AnalysisFrame`. They preserve frame ID,
version, spine, columns, charts and operation history. Finding updates are evidence updates
at the current analytical version, so they do not introduce an `OperationType`, increment
the frame version or append to the analytical operation log. Duplicate IDs, nonexistent
revision targets, unknown current supporting columns and out-of-range evidence fail before
a new frame is returned. The input remains unchanged on every failure.

`ChartSpec` contains chart ID, type (`line`, `bar`, `scatter`), spine key, ordered
column keys, title and `axis_policy="by_unit"`. Optional ordered `axis_assignments`
make the server's left/right decision explicit, and `indexing_recommended` carries a
recommendation without creating a derived column. Axis assignments, when present,
must reference every chart column exactly once and in the same order. Existing specs
without these fields remain valid through empty/false defaults. References must exist
in the current frame. Plotly rendering is not implemented here.

## Closed operation vocabulary

`Operation` is an immutable audit record with operation ID, kind, typed parameters,
timezone-aware timestamp (normalized to UTC), source version and resulting version.
Unknown fields, unsupported kinds and mismatched parameter schemas are rejected.
There is no executable SQL/Python field and no generic expression escape hatch.

| Kind | Parameters | Documented basis |
|---|---|---|
| `add_column` | series_reference, column_key | Demo turns 1/3 and left-join semantics |
| `deflate_column` | column_key, deflator_column_key, base_date, convention_reference | Demo turn 2 |
| `index_column` | column_key, base_date | Supplied backlog: "index_column with original retained" (2026-09-15) |
| `revert_to` | target_version | README/PLAN undo requirement |

The deflation reference identifies the convention settled in DECISIONS.md #9. The target
and deflator must differ. A revert target must precede the operation's source version.
Revert restores retained columns, charts and the identical spine while preserving findings
as append-only evidence. Revert records advance the current version; they never erase the
operation log, evidence history or reset the version counter. Historical findings retain
their original `frame_version`, including findings created after the restored target.

The user-supplied backlog export explicitly includes **"index_column with original
retained"**, due 2026-09-15, referencing PLAN.md D5. Its requirements are rebasing to
100 at a chosen period, showing that period in the label, retaining the original
column and rejecting a missing base observation rather than selecting a neighbour.
`IndexColumnParameters` therefore contains only `column_key` and `base_date`, using
the same calendar-date representation as deflation. Base 100 is fixed, not a
configurable parameter. Execution, label generation, output-column identity and
missing/zero-base numerical checks are deferred to the executor; no arithmetic runs
in this contract.

The export also explicitly names `add_series_column`, `deflate_column`, `revert_to`
and `revise_finding`. `add_column` is this contract's existing name for the series-add
operation; it is not renamed here. Finding revision uses the existing
`Finding.supersedes` link through the append-only agent service and does not expand the
closed analytical operation vocabulary.
The backlog requires retaining the original column for deflation as well as indexing;
future executor work must honor that requirement. This audit does not add unrelated
commands or choose output-key policies.

DECISIONS.md #6 leaves the closed-vocabulary/escape-hatch boundary open; it does not
prohibit the explicitly planned indexing operation. Generic transforms, spine
reslicing and a Python escape hatch remain outside this contract. Reslicing needs
an explicit consent contract before it can be added. Merely constructing an operation executes nothing and does
not prove its requested column exists or that its intended result was computed.
Those preconditions/postconditions belong to the future executor.

## Versioning and mutation boundary

A frame has a stable frame ID and integer version starting at zero. Each operation
advances exactly one version. Snapshots carry the complete, contiguous operation log
from version zero; `len(operations) == version`. Operation IDs are unique within the
log. Finding and chart IDs are unique within their respective collections.

Models are frozen Pydantic objects; nested collections are tuples and nested metadata
contains only immutable scalars. Input lists are copied into tuples. Ordinary in-place
mutation cannot alter the spine, columns or history. To represent a new snapshot,
construct/validate a new `AnalysisFrame` with the same frame ID and retained history.
No arithmetic, joining or undo is performed by model construction.

`candidate.assert_successor_of(previous)` is a validation-only boundary for a future
executor: it checks the frame ID, exactly one version advance, intact spine, an
unchanged operation-history prefix and an unchanged finding-history prefix. It does
not create the successor or verify operation-specific numerical effects. Callers
must use it when accepting a new version: one isolated snapshot cannot prove that a
previously stored history was not rewritten.

Do not use Pydantic `model_construct`, `model_copy(update=...)`, direct `__dict__`
mutation or `object.__setattr__` for untrusted data: these are Python/Pydantic bypasses,
not supported contract mutation APIs. Reconstruct with normal constructors or
`model_validate`/`model_validate_json`; nested instances are revalidated.

## Serialization example

```python
from kkb_agent.frame import AnalysisFrame, Column, Lineage, SourceReference, Spine

frame = AnalysisFrame(
    frame_id="analysis-1",
    spine=Spine(key="period", values=["2025-01-01", "2025-02-01"]),
    columns=[Column(
        key="series-a", label="Series A", dtype="number", values=[10, None],
        origin="source",
        lineage=Lineage(sources=[SourceReference(
            source_type="csv", reference="sample.csv#value",
        )]),
    )],
)
encoded = frame.model_dump_json()
assert AnalysisFrame.model_validate_json(encoded) == frame
```

Public models expose `model_json_schema()`. Serialization preserves tuple ordering,
uses ISO temporal values and JSON nulls, and adds no random IDs or current timestamps.
It is deterministic for the same model, not a canonical JSON signing/hashing format.
The current MIA capability report supports strict JSON schemas, but no MIA schema
submission or provider compatibility test is part of this task.

## Deferred work and open questions

No executor, transformation, persistence, database access, network call, planner,
retrieval, ingestion, analytical tool, API endpoint, SSE, frontend or rendering change
is implemented. Conversation ownership/forking (#7), ragged edges (#11), snapshots/live
refresh (#12) and wider operation boundaries (#6)
remain open in DECISIONS.md. Durable retention/compaction and historical evidence resolution
need future persistence decisions; the executor currently provides explicit instance-scoped
in-memory analytical snapshots and v1 stores complete operation and finding history.

Verification:

```bash
.venv/bin/python -m pytest tests/kkb_agent/frame -q
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
```
