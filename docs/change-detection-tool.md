# Change detection tool

`analyze_anomalies`'s sibling. `detect_changes(values, dates, *, min_size=6,
penalty_scale=2.0)` is a deterministic, in-memory time-series tool. It does not mutate
an `AnalysisFrame` or use an LLM, database, network, or source-specific behavior.

## Algorithm

The tool runs PELT (`ruptures.Pelt(model="l2", jump=1)`) twice per contiguous observed
segment, independently:

- **Level pass** — on the raw values, to find where the series jumps to a new mean.
- **Trend pass** — on the first differences of the same segment, to find where the
  per-period slope changes. This catches a reversal or acceleration that never
  produces a discontinuity in the series itself (a "V" or "peak" shape), which the
  level pass cannot see.

Both passes use the same exact, deterministic PELT search (`jump=1`, no randomness).
The penalty for each pass is computed, not hand-tuned per call:

```text
penalty = penalty_scale * sample_variance(segment_or_diff) * log(n)
```

`sample_variance` is taken over the same values being segmented (raw for the level
pass, differenced for the trend pass), so the two passes are scaled independently.
Raising `penalty_scale` only ever removes breakpoints; it never adds one, since a
higher penalty makes every candidate split more expensive relative to its cost
reduction.

A level breakpoint's `before_value`/`after_value` are the segment means on either
side. A trend breakpoint's `before_value`/`after_value` are the mean per-period slopes
on either side; its reported `index`/`date` is the first point governed by the new
slope (one past the last point of the old regime), since the value at the pivot itself
still belongs to both fitted lines.

## Data requirements and missing values

`min_size` must be an integer of at least 2 and `penalty_scale` must be positive and
finite. At least one uninterrupted observed segment must contain two complete windows
of `min_size` observations (`2 * min_size`); the trend pass additionally needs one more
point in that segment, since differencing shortens it by one. Shorter segments are left
unscored, exactly as in the anomaly tool.

`None` and `NaN` are treated as missing and are never filled or interpolated; a
breakpoint is never inferred across a gap between two observed segments. Infinite
values and non-numeric or boolean inputs are rejected. Dates must be unique and
strictly increasing in input order.

## Output

`ChangeDetectionResult.breakpoints` is a flat, sorted tuple of `ChangePoint` records
(level and trend interleaved, ordered by index). Each carries its index/date, kind,
before/after value, magnitude, and a short explanation. `ChangeDetectionResult.parameters`
records the exact method, `min_size`, `penalty_scale`, the minimum segment length applied,
and the penalty formula, so a result is reproducible and auditable without reading the
source. This output is the intended input to the causality tool's specification step
(SCRUM-57/58): a structural break found here is a candidate point to control for, not a
causal claim in itself.

## Validation status

Accuracy is validated here against synthetic series with known, exactly-placed
injected breakpoints (clean level shifts, a trend reversal with no level jump, and a
combination of both in one series) — see `tests/kkb_agent/tools/test_change_detection.py`.

**Not done here:** plausibility checking against real Turkish series (BDDK/EVDS) is
deferred until the gold layer exists. Per the backlog's own warning, known events (Dec
2021 FX crisis and KKM, the 2023 policy reversal, Feb 2023 earthquake) are useful for a
sanity check once real data is available, but they are not ground truth, and a
breakpoint coinciding with an event date does not mean the event caused it. This tool
must not be tuned to reproduce that list.
