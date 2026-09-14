# Causality method and test sequence

This note fixes the decision policy for a future causality tool. It does not implement a
test. Every accepted result means conditional temporal predictability in the Granger sense;
it does not by itself establish a structural, policy or real-world causal mechanism.

## 1. Current data characteristics

Evidence snapshot: inspected **2026-09-14**. The local EVDS coverage report was generated
**2026-09-12 19:18:03 UTC** for the requested interval 2021-01 through 2026-06. Generated
silver data are gitignored and may differ on another checkout, so a future run must record
the coverage-file hash and re-evaluate this section.

| Frequency | Series | Rows per series | Missingness | Current interpretation |
|---|---:|---:|---|---|
| Weekly | 7 credit-rate series | 287 | 0 | Enough rows for a small VAR only after all later gates pass. |
| Monthly | 13 macro/banking series | 66 | Usually 0 | Enough for diagnostics, below this policy's formal causality floor. |
| Monthly KKM | Included above | 66 | 11 leading nulls; 55 usable | Below the formal floor and has a shorter economic regime. |
| Daily | 2 FX series | 2,007 calendar rows | 631 nulls each | About 1,376 observed business days; calendar closures are not values to interpolate. |

These 22 series are a curated development subset, not the planned full EVDS catalog. BDDK
monthly, weekly and FinTuerk bronze data are acquired, but they are not yet aligned causal
inputs. The inspected `data/gold/` directory is empty. EVDS silver files contain exact raw
observations and retrieval timestamps; they have not been frequency-harmonised, deflated,
seasonally adjusted or otherwise made comparable. Therefore the current checkout cannot
produce a `causality established` result for the published loan-volume/rate question.

Frequency labels must remain separate. Weekly rates cannot be joined directly to monthly
stocks, and neither may be joined directly to daily FX. Aggregation is allowed only through
a deterministic, lineage-recorded rule appropriate to the measure type. Calendar nulls in
daily FX are market closures, while the KKM leading nulls mean the series did not yet exist;
neither case may be replaced with zero.

The available data already show structural-break risk. The SCRUM-56 plausibility run on the
66 monthly `TP.KFE.TR` observations found level-break candidates at 2023-03 and 2025-01 and
trend-break candidates at 2021-12, 2023-01 and 2023-10. These are screening results, not
historical-event labels or causal evidence. Nominal credit, deposit, house-price and CPI
levels also share a strong inflationary environment, so a high correlation in levels is
presumptively vulnerable to a common-trend explanation.

## 2. Test sequence

The future tool must execute these gates in order and retain the decision and parameters at
each gate. A failed hard gate ends with `not identifiable`; later tests are not run to rescue
the claim.

1. **Pre-specify the question.** Record candidate cause, outcome, expected direction,
   frequency, window, transformations, controls and the hypothesis family before inspecting
   p-values.
2. **Verify semantic and data eligibility.** Require a plausible temporal ordering, causal
   input semantics, a common frequency, an exact aligned spine and a contiguous usable
   window. Reject unverified or silently interpolated inputs.
3. **Apply pre-declared transformations.** Use catalog semantics: rates may remain in level
   form if stationary; positive monetary/index levels normally use log levels for long-run
   analysis and log differences for growth. Nominal amounts require the chosen inflation
   treatment. Every transformation must remain in lineage.
4. **Screen structural breaks with SCRUM-56.** Run level and first-difference PELT on the
   analysis transformations, retain its penalty, minimum segment length and jump, and map
   break indices to dates using its typed result.
5. **Diagnose integration order.** Use ADF and KPSS with deterministic terms selected before
   seeing their p-values. Classify only when their opposite null hypotheses give a coherent
   result. Test first differences only after the level decision.
6. **Choose one model branch.** Use a stationary VAR for coherent I(0) inputs, a differenced
   VAR for coherent I(1) inputs without cointegration, or a VECM for adequately sampled I(1)
   inputs with cointegration. Do not run every branch and report the smallest p-value.
7. **Select lag and fit diagnostics.** Select within the fixed frequency cap using BIC, then
   verify stability, residual serial correlation, finite covariance and parameter budget.
8. **Test direction and robustness.** Test the pre-specified lag block, apply multiplicity
   control, and repeat at adjacent admissible lag orders and viable break regimes. Record the
   reverse direction separately.
9. **Assign one result class.** Apply the rules in section 10; never promote a result because
   its date resembles a known Turkish economic event.

## 3. Purpose of each method

| Method | Purpose in this project | What it does not prove |
|---|---|---|
| SCRUM-56 PELT | Detect candidate level/trend regime changes before stationarity and VAR decisions. | A cause, event match or valid coefficient break by itself. |
| ADF | Test the null that the selected deterministic specification contains a unit root. | Stationarity when it merely fails to reject. |
| KPSS | Test the opposite null of level or trend stationarity. | A specific unit-root order when it rejects. |
| Engle-Granger cointegration | For a pre-specified bivariate I(1) pair, test whether a stationary long-run combination is plausible. | Directional causality. |
| Stationary/differenced VAR block test | Test whether lags of X add predictive information for Y conditional on included variables. | Structural causation or absence of omitted confounding. |
| VECM | Separate short-run lag effects and long-run error-correction adjustment for an adequately sampled cointegrated system. | Direction from cointegration alone. |
| Toda-Yamamoto augmented VAR | Optional robustness check when integration is at most I(1) and a stable levels VAR is estimable. | A shortcut around sample size, breaks, instability or missing controls. |

`ADF/KPSS -> cointegration -> Toda-Yamamoto` is therefore rejected as a universal chain.
Cointegration is irrelevant for coherent I(0) pairs, Toda-Yamamoto is not the selected model
for every non-stationary pair, and all three can be unreliable in short, broken samples.

### Method decision

Selected for v1 are the SCRUM-56 break screen, paired ADF/KPSS diagnostics, a stationary VAR
lag-block test for coherent I(0) data, a differenced VAR for adequately sampled I(1) data
without cointegration, and a bivariate Engle-Granger/VECM route for adequately sampled I(1)
pairs with cointegration. Zivot-Andrews is a one-break sensitivity check. Toda-Yamamoto is
allowed only as a pre-declared robustness check and cannot establish the result by itself.

Rejected for v1 are an unconditional regression or correlation between trending levels,
choosing lags by the smallest p-value, interpreting cointegration as direction, running all
model branches and selecting the favourable one, and using Johansen rank search for the
current pairwise questions. Johansen may be reconsidered when a justified system with more
than two endogenous variables and enough observations exists.

## 4. Minimum sample and assumptions

The counts below are conservative **project guardrails**, not universal statistical
theorems. `n` means aligned, non-null observations after transformations and before model
lags. `q` is the number of estimated regressors per equation, including lagged endogenous
variables, deterministic terms, controls and break dummies. For a bivariate VAR with an
intercept and `c` controls/dummies, `q = 1 + 2p + c`; Toda-Yamamoto uses
`q_aug = 1 + 2(p + dmax) + c`.

| Method | Minimum accepted sample | Required assumptions and gate |
|---|---:|---|
| SCRUM-56 PELT screen | At least `2 * min_segment_length + 1` for the differenced signal | Complete finite values, ordered unique dates, scale-dependent penalty recorded. For causality screening use `min_segment_length=12` monthly or `26` weekly and `jump=1`. |
| ADF and KPSS diagnostics | `n >= 50` | Contiguous observations; deterministic terms fixed in advance; residual dependence handled by the recorded lag/bandwidth choice. With `50 <= n < 80`, results are diagnostic only. |
| Zivot-Andrews one-break unit-root sensitivity | `n >= 80` | Exactly one defensible dominant break. Multiple material breaks or an unstable break date cause refusal rather than repeated break hunting. |
| Stationary or differenced bivariate VAR | `n_eff >= max(80, 10q)` | Both inputs coherent I(0), or both coherently I(1) and differenced; stable VAR; residual serial correlation absent at the declared level. |
| Engle-Granger bivariate cointegration | `n >= 100` | Both series coherently I(1), neither I(2), deterministic specification fixed, economically meaningful long-run pair. |
| Bivariate VECM | `n_eff >= max(120, 10q)` | Coherent I(1), cointegration rank one, stable residual diagnostics and identified deterministic terms. |
| Toda-Yamamoto robustness | `n_eff >= max(120, 10q_aug)` | `dmax <= 1`, stable levels VAR, augmented order `p + dmax`, no unresolved structural break. It cannot be the sole basis for `causality established`. |

`n_eff` is the number of rows remaining after lag loss. The current 66-row monthly series
may pass ADF/KPSS and PELT diagnostic floors but fail every formal VAR/VECM floor. The 55
usable KKM observations fail more strongly. The 287-row weekly series may be eligible, but
only for same-frequency questions with adequate semantics and controls.

## 5. Lag selection policy

Lag order is chosen once per model with BIC, not by searching for the lag with the smallest
causality p-value. Candidate caps are `1..3` for monthly, `1..8` for weekly and `1..10` for
observed-business-day daily data. The parameter rule `n_eff >= 10q` can reduce these caps.
Lag zero means there is no lagged predictive-causality test and produces `not identifiable`.

Annual seasonal lags of 12 months or 52 weeks are not added automatically. For 66 monthly
observations a 12-lag bivariate VAR is categorically refused. A seasonal lag may enter only
when the question pre-specifies it, the parameter budget passes and residual diagnostics
support it.

The selected result is rerun at `p-1` and `p+1` when those orders remain admissible. Every
applicable adjacent order must fit and pass verified diagnostics; a failed or unverified
applicable check makes robustness unavailable even if another adjacent order passes. A
change to no direction or bidirectionality is inconclusive. Only an opposite one-way result
is a directional reversal. Any of these outcomes caps otherwise positive evidence. Residual
serial correlation triggers a higher lag only within the cap and parameter budget;
otherwise the tool refuses.

## 6. Structural-break policy

SCRUM-56 is a mandatory screen, using the exact transformed series that will enter the
causality model. Its typed level and trend breakpoints, dates and all parameters become
evidence. The fixed causal-screen settings above prevent selecting a different minimum
segment merely because it gives a desirable result. The scale-based penalty rule used in
the existing KFE plausibility check, `log(n) * variance(signal)`, is the initial screening
rule; sensitivity at `0.5x` and `2x` is reported, not searched for significance.

If no material break is stable across that sensitivity range, continue on the full window.
If a stable break exists, use an externally justified break dummy or estimate pre/post
regimes only when every resulting regime independently meets the model's sample floor.
Results must agree in direction across viable regimes to reach `causality established`.
When breaks leave undersized regimes, occur near an endpoint, or move materially under the
fixed sensitivity check, return `not identifiable`.

PELT is not a unit-root test. Ordinary ADF/KPSS conclusions are not treated as decisive in
the presence of a stable break. A Zivot-Andrews one-break sensitivity test may be used only
at `n >= 80`; multiple unresolved breaks cause refusal under the current scope.

## 7. Non-stationarity and cointegration policy

ADF and KPSS are paired because their null hypotheses are opposite:

| ADF level result | KPSS level result | Classification |
|---|---|---|
| Reject unit root | Do not reject stationarity | Coherent I(0) evidence. |
| Do not reject unit root | Reject stationarity; first differences reverse both decisions | Coherent I(1) evidence. |
| Any other combination | Any other combination | Inconclusive; do not force an integration order. |

Constant-only or constant-plus-trend specifications are selected from series semantics and
plots before p-values are inspected. Running both and choosing the preferred answer is
forbidden. No route supports I(2) in v1.

For a coherent I(1) bivariate pair with `n >= 100`, Engle-Granger is the selected
cointegration diagnostic. Cointegration alone never determines X-to-Y or Y-to-X direction.
At `n >= 120`, rank-one evidence may open the VECM route; short-run lag exclusion and the
error-correction loading are reported separately. Without cointegration, use a differenced
VAR if its floor passes. Mixed I(0)/I(1), inconclusive integration order, or insufficient
sample returns `not identifiable` under v1.

Toda-Yamamoto is retained only as a pre-declared robustness analysis at `n >= 120`. It is
not the default response to uncertainty and cannot override a failed break, stability,
parameter-budget or confounder gate.

## 8. Multiple-testing approach

The hypothesis family is recorded before testing. For one pre-specified pair, the two
directions form one family and Holm correction controls family-wise error at `alpha=0.05`.
Adjacent-lag and regime runs are robustness gates, not extra chances to select a p-value.

For an explicitly exploratory scan over several pairs, apply Benjamini-Hochberg FDR at
`q=0.05` to the final directional p-value from each pre-specified model. Such a scan can
produce candidates only: even an adjusted discovery is capped at `limited evidence` until
confirmed on a held-out period or a separately acquired snapshot. If the family cannot be
defined before inspection, refuse the scan.

Stationarity, cointegration and residual-diagnostic p-values answer different gate
questions and are not pooled with directional hypotheses. Their complete results are still
reported so a reviewer can see every decision.

## 9. Refusal conditions

Return `not identifiable` without a directional p-value when any of these holds:

- The proposed cause has no defensible temporal ordering or the question is contemporaneous.
- Inputs mix monthly, weekly or daily frequencies without a documented deterministic
  aggregation and identical aligned spine.
- Inputs are bronze/silver-only, unverified, silently interpolated, or lack transformation
  lineage required for the question.
- No contiguous common window meets the method's absolute and `10q` sample floors.
- Missing internal periods remain, or dropping them would create an irregular lag interval.
- Nominal trending levels are compared without an explicit inflation/common-trend treatment.
- A material confounder identified before fitting is unavailable and its omission could
  explain the direction.
- SCRUM-56 finds stable breaks but viable regimes are undersized or disagree in direction.
- ADF/KPSS integration classification is inconclusive, suggests I(2), or changes under the
  justified break treatment.
- A required Engle-Granger cointegration test cannot be computed, so the I(1) model branch
  cannot be selected safely.
- BIC selects lag zero, the fitted VAR/VECM is unstable, residual serial correlation remains,
  covariance is singular, or lag estimates exceed the parameter budget.
- Corrected significance, sign or direction is not stable at adjacent admissible lags.
- The multiple-testing family was defined after inspecting results.

Failure to reject a null is reported as insufficient evidence, never proof that causality is
absent.

## 10. Result classes

### `causality established`

Use only for a pre-specified direction when every data, sample, stationarity, break, lag,
stability, residual, confounder and multiplicity gate passes; the corrected directional
test is significant at 0.05; and direction/sign remain stable across adjacent admissible
lags and viable regimes. The displayed sentence must say **predictive/Granger causality
conditional on the included variables**, not structural causation. Bidirectional results
are allowed only when each direction independently passes.

### `limited evidence`

Use when the aligned data are interpretable but evidence is exploratory, sensitive to one
admissible lag or regime, based only on lead/lag and effect-size patterns, or found in an
FDR-controlled scan without hold-out confirmation. Cointegration by itself belongs here at
most. Current 66-row monthly pairs can reach this class descriptively but cannot be promoted
by a formal p-value under this policy.

### `not identifiable`

Use for every hard-gate failure in section 9. Include the failed gate, observed sample size,
required minimum, relevant break/missingness facts and the additional data or decision that
would make a later test possible. Do not execute fallback tests until one becomes
significant.

## 11. Re-evaluation when coverage changes

Re-run this decision note when any of the following changes:

- EVDS coverage expands beyond the 22-series snapshot or extends the monthly history enough
  to cross the 80/100/120-observation floors.
- A gold build supplies verified monthly aggregation, real/nominal transformations,
  seasonal adjustment or aligned BDDK series.
- Missingness patterns change, especially KKM history or the treatment of FX calendar days.
- New frequencies, revised source definitions, bank scopes or structural breaks appear.
- More than two endogenous variables or additional controls materially increase `q` and
  reduce the admissible lag cap.
- A hold-out period or independent snapshot becomes available for exploratory discoveries.

Every future result must record source snapshot identifiers/hashes, actual common window,
frequency, transformations, usable `n`, break parameters, deterministic terms, selected lag,
`q`, test family and correction method. Threshold changes require revising this document;
they must not be made per question.

## References

Repository evidence:

- `data/silver/evds/coverage.json`, generated 2026-09-12 19:18:03 UTC.
- `docs/evds-ingestion.md` for silver-layer transformation limits.
- `docs/change-detection-tool.md` for SCRUM-56 parameters and the KFE plausibility result.
- `docs/data-coverage.md`, inspected 2026-09-14, for BDDK/FinTuerk acquisition status.

Method sources:

- Dickey and Fuller (1979), unit-root inference:
  <https://doi.org/10.1080/01621459.1979.10482531>.
- Kwiatkowski, Phillips, Schmidt and Shin (1992), stationarity-null testing:
  <https://doi.org/10.1016/0304-4076(92)90104-Y>.
- Granger (1969), predictive causality:
  <https://doi.org/10.2307/1912791>.
- Engle and Granger (1987), cointegration and error correction:
  <https://doi.org/10.2307/1913236>.
- Toda and Yamamoto (1995), inference with possibly integrated VARs:
  <https://doi.org/10.1016/0304-4076(94)01616-8>.
- Perron (1989), structural breaks and unit-root conclusions:
  <https://doi.org/10.2307/1913712>.
- Zivot and Andrews (1992), a unit-root test with one estimated break:
  <https://doi.org/10.1080/07350015.1992.10509904>.
- Benjamini and Hochberg (1995), false-discovery-rate control:
  <https://doi.org/10.1111/j.2517-6161.1995.tb02031.x>.
