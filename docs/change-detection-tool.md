# Change detection tool

`detect_changes(values, dates, *, level_penalty, trend_penalty,
minimum_segment_length, jump=1)` detects structural breaks in a complete, ordered time
series. It is deterministic and runs in memory without an LLM, database, catalog,
`AnalysisFrame` or persistence dependency.

## Method and output

The tool runs `ruptures.Pelt(model="l2")` twice: once on the original values for level
changes and once on first differences for trend changes. A breakpoint is the index and date
of the first observation in the new regime. A boundary at index `i` in the differenced signal
therefore maps to index `i + 1` in the original series.

The result carries typed `level` and `trend` breakpoints plus every parameter that affects
the calculation: method, cost model, separate level/trend penalties, minimum segment length,
PELT jump and the trend transformation. `ChangeDetectionResult.for_kind(...)` lets a future
causality tool consume either breakpoint stream without coupling this module to a causality
implementation.

## Data requirements

Values must be finite numeric observations. Dates must be unique and strictly increasing.
The differenced signal must be long enough to contain two complete minimum-length segments.
Missing observations are rejected; callers must choose and disclose a complete computation
window rather than allowing this tool to interpolate or silently remove gaps.

Penalty values are scale-dependent and must be supplied explicitly. A larger penalty returns
fewer breaks. `minimum_segment_length` prevents regimes shorter than the stated observation
count. `jump=1` evaluates every candidate boundary and preserves exact date resolution.

## Validation and interpretation

Synthetic tests inject an exact level break and an exact trend break, then verify the returned
indices and dates. They also test the first-difference offset directly.

The Turkish-series plausibility check used 66 monthly `TP.KFE.TR` observations fetched
directly through `EVDSClient` for 2021-01 through 2026-06. To choose penalties without tuning
to historical events, the check used the fixed scale-based rules
`log(n) * variance(values)` for level and `log(n) * variance(diff(values))` for trend. Their
realized values were `19555.626393304658` and `15.315909126512`, with
`minimum_segment_length=6` and `jump=1`.

The result returned level breaks at 2023-03 and 2025-01, and trend breaks at 2021-12,
2023-01 and 2023-10. These dates are a plausibility observation only. The synthetic tests,
not historical event matching, validate breakpoint accuracy; temporal coincidence does not
identify a cause and is not ground truth.
