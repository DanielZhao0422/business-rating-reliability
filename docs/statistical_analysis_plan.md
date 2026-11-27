# Phase 3 Statistical Analysis Plan

## Status, scope, and design freeze

This document is the Phase 3A protocol for **Business Rating Reliability &
Decision Intelligence: A Statistical Study of Marketplace Seller Ratings and
Customer Order Experiences**. It was specified before Phase 3B model comparison.
It defines estimands, hypotheses, populations, estimators, evaluation rules, and
sensitivity analyses; it contains no fitted Phase 3 model, selected prior
strength, seller ranking, performance estimate, or operational result.

The protocol uses the implemented Phase 2C order eligibility rules unchanged.
The version-controlled defaults are in
`config/statistical_protocol.toml`. Any later deviation must be dated, justified,
reported beside the prespecified analysis, and must not replace an unfavorable
primary result.

Protocol v1.1.0 preserves the original design and amends rating availability,
MAE contrast wording, and sensitivity labels after the creation-date run.
The original v1.0.0 used survey creation before the cutoff for history and
creation on or after it for evaluation. Its results and protocol are retained.
[Temporal availability amendment](temporal_availability_amendment.md)

## Research targets and interpretation

Let \(R_{si}\in\{1,2,3,4,5\}\) be the eligible order-experience rating \(i\)
associated with seller \(s\), and let \(n_s\) be the number observed in the
relevant time window.

1. **Observed mean.** The finite-sample descriptive quantity
   \(\bar R_s=n_s^{-1}\sum_i R_{si}\). It describes observed eligible orders;
   it is not an intrinsic seller-quality score.
2. **Underlying expected rating.** A model-based estimate
   \(\theta_s=E(R_{s,new}\mid\mathcal I_t)\) for a future eligible order
   experience, conditional on information available at time \(t\), population
   continuity, and the stated model. It can change when the marketplace,
   customers, order mix, or seller changes.
3. **Rating uncertainty.** Uncertainty about \(\theta_s\) given a finite,
   selected sample and model assumptions. It does not represent all selection,
   attribution, or platform uncertainty.
4. **Predictive validity.** How closely a historical estimate predicts a
   later observed rating outcome under a locked temporal design.
5. **Ranking stability.** How seller orderings change across estimators,
   resamples, new data, or defensible assumptions.
6. **Operational association.** Observed relationships between historical
   rating estimates and later delivery or fulfillment measures.

These targets are related but not interchangeable. **Estimation accuracy** is
closeness to the unobserved conditional expectation; the study can assess it
only indirectly. **Prediction error** compares a historical estimate with a
later outcome. **Uncertainty calibration** asks whether interval frequencies
match their stated probability under a defined target. **Ranking agreement**
measures order similarity and neither proves low prediction error nor objective
quality. A later seller mean is a noisy evaluation target, not truth.

## Questions, hypotheses, and decision measures

| Research question | Hypothesis | Status | Method | Evaluation metric | Population | Interpretation |
|---|---|---|---|---|---|---|
| Do adjusted estimates predict later ratings better than arithmetic means? | H1: adjusted estimates have lower out-of-sample error, especially with fewer historical ratings. | Primary | Paired temporal comparison of historical arithmetic and tuned shrinkage estimates | Seller-equal MAE primary; RMSE and weighted errors secondary; volume-stratified differences | Sellers with at least one historical and one evaluation rating | Evidence for or against predictive usefulness, not seller quality |
| How sensitive are comparisons to evidence volume and estimator? | H2: method disagreement and sampling instability are greater at lower historical volume. | Secondary | Rank agreement, rank movement, and posterior-draw stability by volume band | Spearman rho, Kendall tau-b, absolute rank change, top-K overlap, rank distributions | Fixed eligible ranking universe | Describes disagreement/stability; not accuracy by itself |
| Does adjustment moderate small-sample extremes? | H3: adjusted estimates lie closer to the historical reference than raw means, with larger movement at lower volume. | Secondary | Compare raw-to-adjusted movement and tail membership by historical-volume band | Absolute score change, distance-to-reference change, extreme/tail membership change | Sellers with at least one historical rating | Mechanical/descriptive shrinkage behavior, not proof of improvement |
| How do rating estimates relate to order experience? | H4: historical estimates are associated with later delivery and fulfillment measures. | Exploratory | Seller-level descriptions and prespecified regression associations | Effect sizes, 95% intervals, model diagnostics; no causal estimand | Eligible later orders/sellers with outcome-specific data | Association conditional on selection and measured covariates |

H1 is the sole primary model-comparison hypothesis, judged on overall
seller-equal MAE. Its historical-volume pattern is planned supportive evidence,
not an additional primary success criterion. H2 and H3 are secondary; H4 is
exploratory. Results will not be promoted or removed according to favorability.
Multiple subgroup and operational estimates will be reported as families with
emphasis on effect sizes and uncertainty. If exploratory \(p\)-values are
reported, both raw values and Benjamini-Hochberg false-discovery-rate-adjusted
values at 0.05 will be shown within each stated outcome family.

## Temporal design and leakage controls

The final cutoff is fixed at **2018-01-01**:

- historical: `review_answer_timestamp < 2018-01-01`;
- evaluation: `review_answer_timestamp >= 2018-01-01` through the last eligible
  answer date. The same timestamp defines the internal fold boundaries.

The prediction-time population is every seller with at least one eligible
historical rating. Final evaluability additionally requires at least one later
rating; that later-outcome requirement defines a retrospective evaluation cohort
and is not information the model could have used at prediction time. No future
count, score, operational outcome, or seller survival indicator may affect a
historical estimate, reference distribution, hyperparameter, or tuning choice.

Historical ratings have equal observation weight when constructing seller means
and the pooled reference. For evaluation, each seller's later outcome is

\[
Y_s=\frac{1}{q_s}\sum_{i=1}^{q_s}R^{eval}_{si},
\]

where \(q_s\ge1\). The primary metric weights sellers equally. Separate weighted
metrics weight seller \(s\) by \(q_s\); they answer a marketplace-review rather
than equal-seller question.

All method comparisons use identical seller and observation populations. The
final evaluation partition remains sealed until the estimator definitions,
internal tuning, and code tests are complete. Evaluation outcomes may be read
only by the final scoring stage. The implementation must assert that every
training timestamp precedes its validation/evaluation boundary and log the
maximum training and minimum outcome timestamps.

## Point estimators

### Arithmetic baseline

For historical ratings,

\[
\widehat\theta^{mean}_s=\bar R_s=\frac{\sum_{i=1}^{n_s}R_{si}}{n_s}.
\]

Ratings must be finite integers on the inclusive 1--5 scale; invalid or missing
ratings are excluded by Phase 2C rather than imputed. A one-rating seller has an
ordinary mean and remains eligible. A seller with no historical rating has no
seller-specific historical estimate and is excluded from prediction, not given
a fabricated mean. The existing validated `arithmetic_mean_rating` function is
the software baseline.

### Empirical-Bayes-style shrinkage

The adjusted point estimate is

\[
\widehat\theta^{adj}_s=
\frac{n_s\bar R_s+mC}{n_s+m},
\]

where \(C\) is the reference mean and \(m>0\) is prior strength in equivalent
ratings. The primary \(C\) is the mean of **all eligible historical ratings** in
the relevant training window. It is review-volume weighted: a seller with more
ratings contributes more observations. A seller-equal reference (the mean of
historical seller means) is a prespecified sensitivity analysis. Neither may use
2018+ ratings.

This formula is a shrinkage point estimator, not automatically a complete
Bayesian posterior and not an interval. It gives the raw and adjusted methods
the same historical ratings; only the adjusted method receives the permitted
training-only population reference and tuned \(m\).

## Prior-strength selection

The candidate grid is fixed at \(m\in\{1,2,5,10,20,50,100\}\). Selection uses
expanding-window chronological validation wholly before 2018:

| Fold | Training observations | Validation observations |
|---|---|---|
| 2017 Q3 | dates before 2017-07-01 | 2017-07-01 through 2017-09-30 |
| 2017 Q4 | dates before 2017-10-01 | 2017-10-01 through 2017-12-31 |

Within each fold, a prediction requires at least one earlier training rating and
one rating in that fold's later validation window. Sellers without earlier data
receive no seller estimate in that fold; sellers without a later rating cannot
provide a fold outcome. Both are reported. Each candidate's reference category
distribution and \(C\) are recomputed from that fold's training observations.

For each eligible seller-fold unit, compare the training estimate with that
seller's validation mean. The primary tuning loss is the unweighted mean of
absolute errors across all eligible seller-fold units. The \(m\) with minimum
loss is selected. Exact or numerical ties within \(10^{-12}\) select the smallest
\(m\), the least shrinkage among equivalent scores. No 2018+ observation may be
used.

An adequacy safeguard is prespecified: both folds must have at least 50 eligible
sellers and together at least 200 seller-fold units. Otherwise tuning is labeled
inadequate and the deterministic fallback \(m=10\) is used and disclosed. The
fallback is a moderate, interpretable ten-equivalent-rating choice fixed before
final evaluation; it is not selected from 2018+ performance.

## Predictive evaluation

For method \(a\), seller error is
\(e_{sa}=\widehat\theta_{sa}-Y_s\).

Primary seller-equal MAE:

\[
MAE_a=\frac1S\sum_{s=1}^{S}|e_{sa}|.
\]

Secondary metrics are

\[
RMSE_a=\sqrt{\frac1S\sum_s e_{sa}^2},\qquad
WMAE_a=\frac{\sum_s q_s|e_{sa}|}{\sum_s q_s},
\]

\[
WRMSE_a=\sqrt{\frac{\sum_s q_s e_{sa}^2}{\sum_s q_s}}.
\]

Results are also reported for historical volume bands 1--4, 5--9, 10--19,
20--49, and 50+, and for the same evaluation-count bands. The broad primary
cohort uses \(q_s\ge1\); secondary cohorts require \(q_s\ge5\) and \(q_s\ge10\),
with retained sellers, ratings, and proportions reported. These thresholds
trade coverage for less noisy later means and are not cleaning rules.

Review-level absolute and squared error may be supplementary. Because ratings
within sellers are clustered and the estimate is seller-level, review-level
results cannot be presented as independent confirmation of seller-level
reliability.

For every metric report both method values and the paired contrast
\(\Delta=L_{adj}-L_{mean}\), where negative values favor adjustment. Also report
\(-\Delta\) as improvement only with its direction labeled. H1 is judged first
on the locked primary MAE, not whichever secondary metric is most favorable.

## Uncertainty model and calibration

### Primary interval model

The primary uncertainty method is an empirical-Bayes categorical model. Let
\(p_s=(p_{s1},\ldots,p_{s5})\) be probabilities for the five rating categories.
The training-only pooled category proportions are
\(\pi=(\pi_1,\ldots,\pi_5)\),
and

\[
p_s\sim Dirichlet(m\pi_1,\ldots,m\pi_5),\qquad
(N_{s1},\ldots,N_{s5})\mid p_s\sim Multinomial(n_s,p_s).
\]

The posterior is \(Dirichlet(m\pi_k+N_{sk})_{k=1}^5\). Its expected star rating
has posterior mean

\[
E(\theta_s\mid N_s)=\sum_{k=1}^5 kE(p_{sk}\mid N_s)
=\frac{n_s\bar R_s+mC}{n_s+m},
\quad C=\sum_{k=1}^5k\pi_k.
\]

Thus the interval model is coherent with the primary adjusted point estimator
rather than a separate scoring model. Equal-tailed 95% posterior credible
intervals for \(\theta_s=\sum k p_{sk}\) use 20,000 seeded Dirichlet draws.
Even for one review, an interval exists because the prior contributes positive
mass; it may be prior-sensitive and must be shown with \(n_s\), \(m\), and
sensitivity results. These are empirical-Bayes **credible** intervals, not
frequentist confidence intervals. They condition on plug-in \(\pi,m\), assume
exchangeability within the window, and can understate hyperparameter, temporal,
clustering, and selection uncertainty.

A nonparametric seller-review bootstrap was rejected as the primary interval
because one-review sellers resample the same value and two-review sellers yield
poor discrete approximations. It remains a possible high-volume sensitivity,
clearly labeled as a bootstrap confidence interval rather than a credible
interval.

### Calibration targets

The observable primary calibration target is a 95% **posterior predictive
interval for the later sample mean of size \(q_s\)**. For each historical
posterior draw, simulate \(q_s\) categorical ratings, average them, and compare
the actual later mean with the resulting equal-tailed interval. Using observed
\(q_s\) defines the evaluation target's sampling precision; it does not change
the historical point estimate. Report empirical coverage and median/mean width
overall and by historical and evaluation volume bands.

Coverage of a posterior interval for latent \(\theta_s\) cannot be directly
tested by checking whether it contains noisy \(Y_s\). Such a comparison will not
be called latent-mean coverage. Secondary checks may include one-future-rating
predictive coverage and simulation-based recovery under the fitted model.
Coverage materially below 95%, especially consistently across volume bands,
challenges predictive calibration; near-nominal coverage with excessively broad
intervals is weak evidence and must be interpreted with width. Narrowness alone
never establishes calibration.

## Ranking comparison and reliability

The primary ranking universe is the fixed final-evaluation cohort: sellers with
at least one historical and one later rating. Historical raw and adjusted scores
are ranked over exactly that universe. Sensitivity analyses use the same fixed
universe within each threshold/cutoff and report population retention.

- Spearman correlation uses average ranks for ties.
- Kendall's tau-b accounts for ties.
- Absolute rank change uses the same average ranks.
- Top-K overlap is \(|TopK_a\cap TopK_b|/K\) for \(K=10,25,50\).
- Exact top-K membership orders by estimate descending, then seller ID ascending
  solely as a reproducible arbitrary tie-break; tied-boundary counts are shown.

Raw-versus-adjusted metrics quantify **method disagreement**. Joint draws of
each seller's expected rating from the prespecified Dirichlet posterior produce
rank distributions and top-K inclusion frequencies that quantify
**model-based sampling uncertainty**. A high-volume sensitivity may resample
historical ratings within each seller at the original \(n_s\); it is not used
for one- or two-rating sellers because the empirical distribution is too
degenerate. The paired seller bootstrap used for metric contrasts is not
misrepresented as within-seller ranking stability. Correlation or pairwise
agreement between a historical ranking and later observed seller means
quantifies limited **predictive ranking usefulness**. These are distinct: high
method agreement is not accuracy, and a large rank change is not improvement.

Historical ranking informativeness is evaluated primarily in the broad cohort
using Kendall tau-b against later observed means; Spearman correlation,
pairwise concordance, and top-K overlap with the later ordering are secondary.
The \(q_s\ge5\) and \(q_s\ge10\) cohorts test sensitivity to noisy future means.
Historical volume-band results preserve representation of low-volume sellers.
No ranking metric tunes \(m\) or changes the final evaluation population.

## Paired comparison and statistical inference

The reported improvement contrast is raw MAE minus adjusted MAE. Positive
values favor adjustment. The primary uncertainty statement is a paired
seller-level percentile bootstrap interval:

1. sample \(S\) eligible sellers with replacement;
2. carry each selected seller's raw estimate, adjusted estimate, later mean,
   later count, and subgroup together;
3. recompute both metrics and their paired contrast;
4. repeat 2,000 times with seed 42; and
5. use the 2.5th and 97.5th percentiles for the 95% interval.

For review-weighted metrics, sellers remain the bootstrap unit and their full
later counts/weights travel with them. This preserves within-seller clustering
and pairing; it does not assume reviews are independent seller-level units.
Report the point contrast, interval, bootstrap Monte Carlo settings, and cohort.
An interval compatible with zero is inconclusive, not proof of equivalence; an
interval excluding zero is not evidence of causality or universal superiority.
No separate null-hypothesis test is primary.

## Analysis populations

| Population | Eligibility and denominator | Purpose | Main selection risk |
|---|---|---|---|
| Full descriptive | Every Phase 2C eligible order rating and every represented seller; no volume threshold | Describe ratings, volume, and coverage | Attribution and review-participation selection |
| Historical estimation | Seller has at least one eligible rating before cutoff; denominator is eligible historical ratings/sellers | Construct historical raw and adjusted estimates | Excludes sellers first observed later |
| Internal validation | Per fold, at least one earlier training and one later fold rating; denominator is seller-fold units | Select \(m\) without final-period leakage | Favors sellers active across each fold |
| Final temporal evaluation | At least one historical and one 2018+ rating; denominator is sellers for primary metrics | Out-of-time prediction comparison | Survivorship/activity and noisy later means |
| Ranking comparison | Same fixed final-evaluation sellers for both methods | Method agreement and later-ranking usefulness | Ranking depends on included universe |
| Uncertainty estimation | Historical sellers with at least one rating; calibration additionally requires a later outcome | Credible intervals and predictive calibration | Model/prior sensitivity; evaluability selection |
| Operational association | Historical estimate plus outcome-specific eligible later orders; denominators reported separately by outcome | Noncausal later operational associations | Outcome missingness, order mix, survivor selection |

Method-specific historical thresholds 5, 10, and 20 and evaluation thresholds 5
and 10 are sensitivities only. Every output must show eligible seller/order
counts, missing-outcome counts, and the source denominator. Low-volume sellers
remain in the descriptive and primary broad populations.

## Operational association framework

Historical arithmetic and adjusted estimates are the rating exposures. Primary
operational outcomes come from Phase 2C orders purchased on or after the cutoff
whose rating also becomes available in the evaluation period, keeping the same
cutoff and seller attribution:

- delivered-late indicator and delivery-delay days when both estimated and
  actual delivery timestamps exist;
- fulfillment status for all outcome-eligible orders;
- total item value, total freight value, and their ratio where valid; and
- product-category composition as context, not an intrinsic seller attribute.

The estimate is fixed at 2018-01-01. Requiring
`purchase_timestamp >= 2018-01-01` makes the primary operational cohort a set
of future-initiated orders; missing purchase timestamps are reported and
excluded from that cohort. Future operational outcomes are outcomes, never
historical predictors. Fulfillment status is a source snapshot without a
complete status-event history and is therefore exploratory. If historical
operational controls are later used, they must be computed only from orders
whose required fields were available before the cutoff. Outcome timestamps and
availability are logged explicitly.

Every operational table begins from one row per `order_id` and joins the
one-row-per-order `seller_operational_features` table before aggregation.
Category joins occur only after order totals are fixed. For composition, a
multi-category order contributes \(1/c_o\) to each of its \(c_o\) known
categories so its total weight remains one; missing category is a labeled group.

Missing actual or estimated delivery timestamps produce missing lateness, never
"on time." Report missingness by fulfillment status and rating/volume groups;
use complete cases for lateness, retain non-delivered statuses as separate
fulfillment outcomes, and do not impute primary outcomes. Sensitivity analyses
may bound missing late outcomes as all late versus all not late.

Primary descriptions use seller-level later late rates, mean/median delay, and
status proportions with Spearman associations to each historical estimator.
Exploratory regressions may use binomial models for late/fulfillment outcomes and
robust linear or quantile models for delay/value, with seller-clustered
uncertainty for order-level models. Prespecified controls may include historical
rating count, pre-cutoff operational history, future order value/freight and
category composition as contemporaneous context, and calendar period. Report
unadjusted and adjusted estimates separately and diagnose nonlinearity and
influential sellers.

Potential confounders include product/category mix, order value, freight,
distance/geography proxies, season, seller volume, customer selection, logistics
providers, platform changes, and unmeasured seller/customer characteristics.
Future order characteristics are not prediction-time controls and change the
conditional association being described. No coefficient is a causal effect.

## Sensitivity analyses

All completed checks, including null or adverse results, will be retained:

1. every fixed \(m\) in the candidate grid, while keeping tuned \(m\) primary;
2. pooled-rating versus seller-equal historical reference means;
3. minimum historical counts 1, 5, 10, and 20, with retention;
4. evaluation minimum counts 1, 5, and 10;
5. alternative audited cutoffs 2017-07-01, 2018-02-03, and 2018-04-01, with
   2018-01-01 always primary; each alternative reports the complete fixed
   \(m\) grid and uses prespecified \(m=10\) only as its summary value, so no
   prior strength is selected using observations that are future under that
   alternative cutoff;
6. historical variability groups defined before outcomes (zero variance,
   positive variance below/above the eligible-seller median, and a separate
   one-rating group where variance is undefined);
7. delivery-performance and product-category subgroups where denominators are
   adequate, labeled exploratory and protected against order duplication;
8. Dirichlet prior/reference alternatives and a high-volume nonparametric
   interval sensitivity; and
9. missing-delivery worst-case bounds.

Subgroup boundaries based on a median are computed from historical data only.
Subgroups with fewer than 30 eligible sellers or 100 eligible outcome orders are
not given regression estimates; their retention counts are reported and any
safe summary is labeled sparse. Sparse groups are not merged after seeing
effects.

## Reproducibility, quality control, and Phase 3B contract

The protocol defaults are justified as follows: the calendar cutoff was fixed in
Phase 2B; the 1--5 bounds are source-valid values; the logarithmically spaced
\(m\) grid covers weak through strong shrinkage without outcome-adaptive search;
seller-equal MAE is robust, interpretable, and matches the seller-comparison
question; top-K values cover narrow and broader decision lists; 95% is a standard
interval level; 2,000 bootstrap resamples balance percentile stability and local
cost; 20,000 posterior draws make quantile Monte Carlo error small relative to
display precision; and seed 42 makes stochastic work reproducible. None may be
silently changed after final outcomes are viewed.

Phase 3B must:

1. load and validate the TOML with standard-library `tomllib`;
2. query Phase 2C tables without changing them and reconcile population counts;
3. implement fold-isolated reference estimation and tuning with timestamp
   assertions;
4. keep final evaluation access separate from tuning;
5. produce deterministic seller-level inputs shared by both methods;
6. retain seller/order denominators and exclusion reasons in every result;
7. test formulas on synthetic fixtures, including one-rating sellers, ties,
   missing outcomes, category duplication, zero pooled category counts, and
   leakage failures;
8. record package/code/config versions, seeds, selected \(m\), fold counts, and
   input database metadata;
9. output local, Git-ignored real-data results only; and
10. distinguish primary, secondary, sensitivity, and exploratory outputs.

The design review found no conflict with prior approved decisions. It resolves
the previously open interval choice with a Dirichlet model whose posterior mean
matches the shrinkage score, locks seller weighting and tie rules, distinguishes
prediction-time from evaluation eligibility, and prohibits circular selection.
Remaining limitations include nonrepresentative reviews, seller/order
dependence, temporal drift, plug-in prior uncertainty, noisy evaluation means,
survivorship, attribution exclusions, multiple exploratory comparisons, and no
objective seller-quality or causal estimand.

The audit's 2016-10-02 date is the start of all valid source reviews. Direct
Phase 2C inspection shows the seller-eligible analytical observations begin on
2016-10-06; the shared end date is 2018-08-31. This population distinction does
not alter the reconciled eligible counts or the fixed cutoff.

## Phase 3B implementation record

Phase 3B implements this locked plan without changing its cutoff, estimator,
tuning grid, primary metric, uncertainty model, ranking rules, or inference
settings. The implementation clarification for a pooled category probability of
zero is a degenerate, lower-dimensional Dirichlet on positive-probability
categories. The omitted category receives probability zero in every draw. This
is mathematically valid on the reduced simplex, avoids invalid zero
concentrations, introduces no pseudocount, and preserves the specified reference
mean and shrinkage equivalence.

The CLI writes only aggregate local reports and records the database and
protocol SHA-256 fingerprints, software versions, seeds, cutoff checks, and
population reconciliation. Numerical real-data results are not copied into this
version-controlled protocol while publication permission remains conditional.
Phase 3C retains responsibility for alternative cutoffs, alternative reference
weighting, extended prior sensitivity, operational associations, and broader
robustness analysis.

## Phase 3C implementation record

Phase 3C executes these prespecified sensitivities without changing the primary
experiment. In particular, the locked alternative-cutoff rule reports every
fixed grid value and uses `m = 10` as the summary; it does not retune on later
outcomes under each cutoff. Tie-aware overlap bounds supplement deterministic
top-K membership, and calibration reports coverage together with interval width
and miss direction.

The operational implementation uses forward-purchased orders and distinct
outcome denominators, preserves non-delivered statuses, and uses seller-clustered
uncertainty for exploratory logistic associations. Full implementation details
are in [`phase3c_methodology.md`](phase3c_methodology.md). This record documents
execution and clarifications; it does not revise the locked primary design.
