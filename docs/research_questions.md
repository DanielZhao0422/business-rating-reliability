# Research Questions

## Primary research question

> Can statistically adjusted customer ratings provide more reliable marketplace
> seller comparisons than simple average ratings, particularly when sellers have
> different review volumes?

This is an investigative question. Statistical adjustment is not assumed to be
better. A method must earn that conclusion through clearly defined reliability,
stability, uncertainty, and out-of-sample evaluation criteria.

## Secondary research question

> How do these rating estimates relate to measurable customer order experiences,
> such as delivery timeliness and fulfillment outcomes?

These relationships are observational associations. The study will not claim
that rating estimates cause operational outcomes or that operational outcomes
alone cause customer ratings.

## Analytical interpretation

The primary analytical quantity is **customer order experience ratings
associated with marketplace sellers**. Olist reviews are recorded at the order
level and may reflect product quality, delivery, expectations, seller-related
service, logistics providers, or marketplace processes. "Seller rating" is a
short label for a seller-associated summary of those observations, not an
objective measurement of a seller's intrinsic quality.

## Measurable subquestions

1. How do the distribution and volume of usable 1--5 order-experience ratings
   vary across sellers, time, and relevant product or operational groups?
2. How much do empirical-Bayes-style shrinkage estimates differ from arithmetic
   seller means, especially for low-review-count sellers?
3. How does statistical uncertainty vary with review count and observed rating
   dispersion, and how should that uncertainty be communicated separately from
   a point estimate?
4. How stable are seller comparisons under new-review arrival, alternative
   prior strengths, reference populations, and other defensible assumptions?
5. Using only information available before 2018-01-01, do adjusted estimates
   predict ratings observed on or after that date better than arithmetic means
   under prespecified error and calibration measures?
6. How are historical rating estimates associated with later delivery
   timeliness, fulfillment status, freight, order characteristics, and relevant
   product information?
7. Which dashboard views help decision-makers distinguish score level, evidence
   volume, uncertainty, predictive validity, and ranking stability without
   implying false precision?

## Prespecified hypotheses

- **H1 (primary):** the historical empirical-Bayes-style adjusted estimate has
  lower overall seller-equal out-of-sample MAE than the historical arithmetic
  mean. Reporting by historical review volume is planned supportive analysis,
  not a separate primary success criterion.
- **H2 (secondary):** estimator disagreement and sampling-based ranking
  instability are greater among sellers with fewer historical ratings.
- **H3 (secondary):** shrinkage reduces distance from the historical reference
  and historical tail membership more strongly for low-volume sellers.
- **H4 (exploratory):** historical rating estimates are associated with later
  delivery and fulfillment outcomes, without a causal interpretation.

These hypotheses were retained before final evaluation. H1 is evaluated first
on the locked primary metric; secondary or subgroup findings cannot replace it.
The estimands, formulas, populations, tie rules, and sensitivity analyses are in
[`docs/statistical_analysis_plan.md`](statistical_analysis_plan.md).

## Intended outcome measures

Phase 3A fixes seller-equal MAE as the primary predictive metric. Secondary
measures are seller-level RMSE, review-weighted MAE/RMSE, predictive-interval
coverage and width, Spearman and Kendall agreement, absolute rank movement,
top-K overlap for K = 10, 25, and 50, and prespecified volume-stratified
results. Operational analysis uses descriptive and regression associations with
outcome-specific denominators.

Later observed ratings are evaluation targets, not ground truth for seller
quality. Reliability, statistical uncertainty, predictive validity, ranking
stability, and operational association answer different questions and must not
be presented as interchangeable evidence.

## Scope guardrails

- Keep low-review-count sellers in the descriptive population.
- Apply method-specific eligibility thresholds only when justified and report
  their population effects.
- Never use future reviews to estimate historical priors, parameters, or scores.
- Do not infer causation from observational associations.
- Keep original Olist data local and treat public publication as conditional on
  CC BY-NC-SA 4.0 obligations and unresolved underlying-rights considerations.
- Preserve the broader motivation: evaluating how much confidence decision-makers
  should place in comparisons derived from customer feedback.

## Phase 3C evidence status

The primary H1 evaluation remains the locked Phase 3B experiment. Phase 3C
assesses robustness without selecting a new final-period prior strength. H2 and
H3 receive secondary and exploratory evidence through tie-aware rankings,
posterior ordering support, count thresholds, and historical variability.
H4 is addressed only through exploratory, forward-timed operational
associations. No operational result is interpreted causally, and no hypothesis
is redefined after observing Phase 3C results.
