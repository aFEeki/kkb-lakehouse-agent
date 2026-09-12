# Anomaly tool

`analyze_anomalies(values, dates, *, period, sensitivity=3.5)` is a deterministic,
in-memory time-series tool. It does not mutate an `AnalysisFrame` or use an LLM,
database, catalog, network, or source-specific behavior.

## Algorithm

The tool decomposes every sufficiently long contiguous observed segment with
`statsmodels` STL using the requested seasonal period and `robust=True`. It then
scores each segment's residuals against that same segment's distribution with:

```text
robust_z = (residual - median(residual)) / (1.4826 * MAD(residual))
```

An observation is anomalous when `abs(robust_z) >= sensitivity`. The default
sensitivity is **3.5**. Lower values flag more observations; higher values flag
fewer. Robust STL removes recurring seasonal structure before scoring, so a normal
seasonal peak is assessed against its local trend and seasonal regime rather than
against the raw series level. Segment-local scoring also prevents the residual
volatility of one observed regime from changing scores in another regime.

Each anomaly includes its input index/date, observed and STL-expected values,
residual, signed robust z-score, direction, absolute score magnitude, and a short
explanation. The result also returns aligned residual and score arrays plus the
parameters used.

## Data requirements and missing values

`period` must be an integer of at least 2 and `sensitivity` must be positive and
finite. At least one uninterrupted observed segment must contain two complete
seasonal periods (`2 * period` observations). Shorter segments are left unscored.
Dates must be unique and strictly increasing in input order.

`None` and `NaN` are treated as missing. The tool neither fills nor interpolates
them; missing positions and positions in short fragments have `None` residuals and
scores and cannot be anomalies. Infinite values are rejected.

If MAD is numerically zero, the denominator uses a small scale derived from machine
precision and the data magnitude. This keeps scores finite and makes constant
series return safely without anomalies while retaining isolated nonzero residuals.

## Limitations

The caller must supply the correct seasonal period. Separate observed segments are
decomposed independently, so trend and seasonal estimates do not cross gaps. STL
identifies departures from a repeating local regime; it does not establish causes
or distinguish data errors from real events.
