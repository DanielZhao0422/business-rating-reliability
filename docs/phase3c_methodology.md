# Phase 3C Methodology

Phase 3C implements the sensitivity, ranking-reliability, calibration, and
operational-association analyses prespecified around the locked Phase 3A/3B
experiment. It does not retune or replace the primary 2018 comparison.

## Analysis classification

- **Locked primary:** the Phase 3B cutoff, pooled reference, historically tuned
  prior strength, seller-equal MAE, and paired seller bootstrap.
- **Preplanned sensitivity:** the fixed prior grid, seller-equal categorical
  reference, historical thresholds 1, 5, 10 and 20, evaluation thresholds 5
  and 10 above the primary minimum of 1, and alternative cutoffs.
- **Supplemental sensitivity:** historical minimum 50 and retrospective
  evaluation minima 3 and 20. These were outside the original locked list.
- **Exploratory diagnostics:** historical rating variability, detailed
  calibration diagnostics, sampled-pair ordering support, and operational
  associations.

The locked plan requires every alternative cutoff to report the complete fixed
prior-strength grid and uses `m = 10` as its prespecified summary. Later outcomes
under an alternative cutoff therefore never select its summary prior strength.

## Robustness methods

Prior-strength sensitivity holds the 2018 population and pooled historical
reference fixed while evaluating every configured value. Seller-equal reference
weighting first calculates each historical seller's five-category proportions,
then averages those vectors equally; it does not merely average seller means.
Historical minimums of 1, 5, 10, 20, and 50 and retrospective evaluation
minimums of 1, 3, 5, 10, and 20 report coverage as well as error. Alternative
cutoffs are 2017-07-01, 2018-02-03, and 2018-04-01.

Sample variance uses `ddof=1`. It is undefined, not zero, for a one-rating
seller. Remaining sellers are separated into zero variance and positive
variance below or above the historical-only positive-variance median.

## Tie-aware ranking reliability

Spearman correlation uses average ranks and Kendall tau-b accounts for ties.
Deterministic seller-ID ordering makes exported top-K sets reproducible but has
no substantive interpretation inside a score tie. At each K, Phase 3C therefore
reports the minimum and maximum overlap possible over all orderings of the raw
boundary tie. Fractional overlap assumes uniform random ordering within that
tie.

Posterior pairwise reliability uses a seed-fixed uniform sample of 50,000 from
all eligible seller pairs and 5,000 batched Dirichlet posterior draws. Ordering
support is conditional on the empirical-Bayes model and observed ratings; it is
not a probability of objective seller superiority.

## Calibration diagnostics

The primary predictive intervals remain conditional on each seller's realized
future review count without using future rating values in construction. Phase
3C classifies later means below, inside, or above the interval and reports
coverage and width by historical volume, evaluation volume, historical
variability, and evaluation quarter. Prior-strength coverage/width curves are
exploratory and do not recalibrate the primary model.

Marketplace and paired-seller rating shifts are descriptive diagnostics. They
cannot distinguish temporal drift from dependence, selection, prior plug-in
uncertainty, or model misspecification.

## Operational cohort and outcomes

The forward operational cohort requires:

1. a seller-specific rating estimate from reviews answered before 2018-01-01;
2. an order purchased on or after 2018-01-01; and
3. its associated review answered on or after 2018-01-01.

Purchase time defines the forward operational event. The answer-time condition
preserves the requirement for a later observed review in this selected cohort.
The purchase condition prevents a late answer to an earlier order from turning
it into a forward purchase. [Temporal availability amendment](temporal_availability_amendment.md) The primary operational outcome is timestamp-level
late delivery among orders marked delivered with nonmissing estimated and actual
delivery timestamps. Cancelled and other non-delivered orders are not classified
as on time. The unresolved-delivered sensitivity bounds assign only delivered
orders with missing timing data to all-non-late or all-late extremes.

Seller-level Spearman/Pearson associations, rating quartiles, and order-level
logistic regressions compare raw and adjusted scores on identical samples.
Logistic uncertainty uses a seller-clustered sandwich covariance. Historical
models include standardized rating and log historical count. Contextual models
add future-order item and freight values, explicitly labeling those variables as
contemporaneous rather than prediction-time seller information.

## Reproducibility and publication

Run `rating_reliability.phase3c_analysis` after the historical evaluation
using the commands in the [reproduction guide](reproducibility.md#build-and-analyze).

The CLI checks the protocol and every required Phase 3B artifact, reproduces the
selected prior strength and primary MAEs, stages all outputs, and confirms Phase
3B checksums are unchanged before publication. Local outputs contain aggregates
and charts. They stay Git-ignored. The public case study and browser demonstration
use selected aggregates under the [publication boundary](data_attribution.md).
