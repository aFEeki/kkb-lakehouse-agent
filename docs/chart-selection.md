# Deterministic chart selection

`kkb_agent.tools.select_chart` creates an explicit `ChartSpec` from an
`AnalysisFrame`. It does not render a chart, mutate the frame, create indexed columns,
or call a planner or model.

## Column and axis rules

Only numeric columns are eligible. When callers provide `column_keys`, those keys are
treated as a selection set; output always follows the frame's insertion order. Every
selected column must have an explicit unit symbol, positive unit scale and
`measure_type`. Missing metadata produces a typed `ChartSelectionError` rather than an
inferred unit.

The unit-group identity is the exact tuple `(unit.symbol, unit.scale, measure_type)`.
The first group encountered in frame order is assigned to the left axis. A second
distinct group is assigned to the right axis. Columns in an existing group reuse that
group's axis. More than two groups produce the deterministic
`too_many_unit_groups` refusal because a dual-axis chart cannot represent them without
an additional product decision.

The default chart type is `line`, matching the temporal AnalysisFrame spine. The
selector emits the ordered column-to-axis mapping in `ChartSpec.axis_assignments`, so a
client can render the server-supplied decision without regrouping units.

## Magnitude recommendation

The fixed indexing recommendation threshold is `100.0`, representing two orders of
magnitude. For each column, the selector takes the median of its absolute, nonzero,
non-null numeric observations. It divides the largest usable column median by the
smallest. A ratio greater than or equal to `100.0` sets
`indexing_recommended=true`. Negative signs do not affect magnitude; zeros and nulls
are excluded. A column with no usable nonzero observation is omitted from this check,
and fewer than two usable column medians produces no recommendation. AnalysisFrame
validation already rejects NaN and infinity.

This flag is advisory only. It never rebases data or appends a column.

## Override and stable identity

`ChartSelectionOverride` is an immutable, closed contract. It may change the chart
type, title or complete axis mapping. An override mapping must reference every selected
column exactly once in frame order, may use only `left` and `right`, and cannot use a
right axis without a left axis. Unknown, missing, duplicate or reordered references are
rejected. No LLM or planner invocation occurs in this layer.

The chart ID is `chart-` plus the first 16 hexadecimal characters of SHA-256 over a
canonical JSON identity. The identity contains frame ID and version, spine key,
ordered column keys, chart type, ordered axis assignments, recommendation, fixed
threshold and title. The same validated frame and selector parameters therefore
produce the same ID and identical spec across runs.

## Transport compatibility

`axis_assignments` defaults to an empty tuple and `indexing_recommended` defaults to
false, so pre-SCRUM-77 `ChartSpec` payloads remain valid. The SCRUM-68 SSE schema is
generated from the Python contract, and its successful fixture carries explicit
left/right assignments to prevent transport drift.
