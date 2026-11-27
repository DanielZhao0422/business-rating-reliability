# Methodology

## Status and analytical scope

Phase 1 baselines, Phase 2A synthetic ingestion, reusable profiling, the Olist
suitability audit, Phase 2C analytical table materialization, the Phase 3A
statistical protocol, Phase 3B historical evaluation, Phase 3C robustness and
operational research, and Phase 4A Power BI architecture are complete. Phase 4B
dashboard construction remains planned. The complete prespecified protocol is in
[`docs/statistical_analysis_plan.md`](statistical_analysis_plan.md), with
machine-readable defaults in `config/statistical_protocol.toml`.

The study examines **customer order experience ratings associated with
marketplace sellers**. "Seller rating" and "seller ranking" are convenient
labels for summaries of these observations. They do not measure intrinsic seller
quality: an order rating may reflect product quality, expectations, delivery,
seller-related service, logistics providers, or marketplace processes.

## Seller-associated observation definition

One usable rating observation must have:

1. exactly one distinct seller among all items in the order;
2. exactly one review row for the order;
3. a review identifier linked to only one order;
4. valid order and seller identifiers;
5. an integer rating from 1 through 5; and
6. a valid survey creation timestamp and a finite `review_answer_timestamp`
   equal to or later than creation.

The order ID is the analytical observation key because Olist `review_id` is not
globally unique. Multiple items from the same seller do not make an order
multi-seller. The Phase 2C `seller_rating_observations` table implements this
rule with 95,245 usable observations for 3,064 sellers. The complete population
flow and exclusions remain available in `order_eligibility`.

## Analytical concepts

These concepts are related but not interchangeable:

- **Reliability** concerns how defensible and repeatable a comparison is given
  its evidence volume, variability, and assumptions.
- **Statistical uncertainty** quantifies the range or variability compatible
  with the observed sample and model assumptions.
- **Predictive validity** evaluates how well an estimate anticipates later
  observed ratings under a prespecified temporal design.
- **Ranking stability** measures how relative positions change with new data,
  resampling, time windows, or defensible modeling choices.
- **Operational association** describes relationships between rating estimates
  and delivery or fulfillment measures without establishing causation.

A narrower interval is not automatically more accurate, a stable rank is not
automatically fair, and better future-rating prediction does not establish true
seller quality.

## Finalized Phase 3 statistical methods

### Arithmetic-mean baseline

For seller $j$, the observed average is the arithmetic mean of eligible 1--5
ratings. It is transparent but ignores how much evidence produced the mean. The
existing `arithmetic_mean_rating` implementation remains the baseline.

### Empirical-Bayes-style adjusted rating

The existing shrinkage form is

\[
\frac{n_j\bar{x}_j + m\mu}{n_j + m},
\]

where $n_j$ is the seller's usable rating count, $\bar{x}_j$ is the observed
mean, $\mu$ is a documented reference mean, and $m > 0$ is the prior
strength. Small samples shrink more toward the reference; large samples remain
closer to their observed means. The reference population and prior strength must
be estimated or selected without future leakage and tested through sensitivity
analysis. This is a point estimator, not an uncertainty interval.

### Uncertainty

The primary uncertainty model is a five-category empirical-Bayes Dirichlet
model. Its prior category probabilities are the pooled historical category
distribution and its concentration is the same tuned prior strength used by the
adjusted estimator. Its posterior mean expected rating therefore equals the
shrinkage point estimate. Equal-tailed 95% posterior credible intervals describe
the model-based expected rating. Posterior predictive intervals for a later
sample mean provide the primary observable calibration target. Credible and
confidence intervals are not treated as interchangeable, and model-based
uncertainty cannot correct review participation or population selection bias.

### Ranking stability

Implemented measures are Spearman correlation, Kendall tau-b, absolute rank
movement, top-K overlap for K = 10, 25, and 50, and posterior-draw stability,
all over a fixed method-comparison universe. Method disagreement, model-based
rank uncertainty, and agreement with later observed rankings are reported
separately in local Phase 3 artifacts.

### Operational associations

Phase 3C compares historical raw and adjusted estimates with later delivery
timeliness, fulfillment status, freight, order value, and relevant product
information. One-row-per-order aggregation precedes category joins, missing
delivery outcomes are not coded as on time, and outcome-specific denominators
are reported. These analyses are descriptive or predictive associations. They
account for time ordering and measured confounders where practical but are not
causal.

## Phase 3B implementation

`rating_reliability.statistical_analysis` is the reproducible orchestrator.
DuckDB performs time-bounded seller and category aggregation; NumPy performs
vectorized estimation, Monte Carlo simulation, rank calculations, and paired
bootstrap inference. The implementation:

1. validates the Phase 2C tables, persistent quality checks, pipeline cutoff,
   and independent historical/evaluation split;
2. aggregates one deterministic row per seller with five category counts;
3. estimates every fold-specific and final reference from training ratings only;
4. selects prior strength from the locked grid with the locked adequacy fallback;
5. verifies the Dirichlet posterior mean equals the shrinkage estimate;
6. conditions predictive intervals on the realized later sample size without
   reading later rating values into their construction;
7. evaluates both methods over one paired seller universe;
8. preserves seller pairing in bootstrap resampling; and
9. stages all aggregate reports before safe local replacement.

The zero-category implementation is a lower-dimensional Dirichlet over
categories having positive pooled training probability. A zero-probability
category stays exactly zero and is never passed as a concentration parameter to
NumPy. This preserves the locked pooled reference and exact posterior-mean /
shrinkage equivalence without adding an unplanned pseudocount.

Credible-interval, predictive-interval, ranking, and bootstrap random streams
are deterministic and recorded in reproducibility metadata. Seller-level scores,
ranks, and identifiers are not exported.

## Primary historical evaluation design

The fixed primary cutoff is **January 1, 2018**:

- historical period: `review_answer_timestamp < 2018-01-01`;
- evaluation period: `review_answer_timestamp >= 2018-01-01`, through the last
  eligible answer date.

The original creation-date split counted 40,976 historical and 54,269 later
ratings. The corrected answer-time split counts 40,321 historical and 54,924
later ratings, 1,098 paired sellers, and 429 sellers with at least 10 ratings
in each period. [Temporal availability amendment](temporal_availability_amendment.md) The cutoff has a simple calendar interpretation and sufficient
two-period coverage. It was designated before statistical method comparison and
was not selected for favorable results. Alternative cutoffs may be sensitivity
analyses.

For every historical prediction:

- calculate seller estimates from pre-cutoff ratings only;
- estimate pooled means, prior strength, hyperparameters, and other model
  components using pre-cutoff information only;
- determine historical eligibility without consulting later counts or outcomes;
- keep evaluation ratings isolated until estimates and comparison rules are
  fixed; and
- treat later ratings as out-of-sample observations, not ground truth for
  intrinsic quality.

The primary final metric is seller-equal MAE over sellers with at least one
historical and one evaluation rating. Prior strength is selected only through
pre-2018 expanding-window validation over the fixed grid 1, 2, 5, 10, 20, 50,
and 100. Paired seller bootstrap intervals quantify raw MAE minus adjusted MAE.
Positive improvement favors adjustment. Exact
rules and deterministic fallbacks are fixed in the dedicated plan before Phase
3B begins.

## Selection and missing-data limitations

Excluding multi-seller orders avoids assigning one order-level score to several
sellers, but those orders differ from single-seller orders and their removal may
change the analytical population. Excluding repeated or cross-order review
identifiers is conservative but may also be selective. Invalid required records
will be reported rather than silently discarded.

Low-review-count sellers remain in descriptive work. Method-specific thresholds
are permitted only when mathematically or evaluatively justified, with full
population-flow counts and sensitivity analysis. No rating is removed merely
because it is 1 or 5, and missing reviews are not imputed as positive or negative.

Other limitations include review participation, marketplace selection,
survivorship, category and geographic imbalance, changing platform conditions,
and possible dependence among orders or reviews.

## Completed supporting infrastructure

Phase 2A's fictional Yelp-style JSONL pipeline established incremental reading,
validation, deterministic duplicate handling, orphan exclusion, quality
reporting, and safe DuckDB publication. It remains tested but is not presented
as the implemented Olist adapter. The Olist audit reads the actual relational
CSVs directly and preserves their source semantics.

The audit verifies software and dataset suitability; it does not rank sellers or
establish the effectiveness of shrinkage. Original Olist files and real-data
outputs remain local while public use is conditional on the published CC
BY-NC-SA 4.0 terms and unresolved underlying-rights considerations.

Phase 2C verifies and materializes the eligible data population; it likewise
does not calculate Phase 3 estimates or comparisons. Operational features that
become known after purchase cannot be used as historical predictors before their
availability.

## Dashboard and reporting plan

The Phase 4A Power BI specification defines:

1. Marketplace Rating Overview
2. Rating Reliability & Uncertainty
3. Ranking Stability
4. Historical Evaluation
5. Delivery & Fulfillment Insights

The Phase 4B dashboard should explain raw versus adjusted estimates, uncertainty, review-volume
effects, robustness, out-of-sample outcomes, and operational associations. It
will not become a generic revenue or marketplace sales dashboard.

The implementation-ready model, KPI dictionary, wireframes, validation matrix,
and local refresh plan are documented in the `powerbi_*` files under `docs/`.
Phase 4A creates no populated export and no `.pbix`.

## Phase 3C implementation

Phase 3C is complete. It preserves the locked Phase 3B comparison and implements
the preplanned prior, reference, configured count-threshold, and cutoff
sensitivities. The historical minimum of 50 and later minima of 3 and 20 are
supplemental. Phase 3C
also resolves raw-score top-K ties with overlap bounds and fractional membership,
diagnoses predictive coverage and miss direction, and analyzes later delivery
outcomes with identical raw/adjusted populations and seller-clustered uncertainty.

The operational cohort requires purchase and the associated review answer on or
after 2018-01-01. The rating predictors use only pre-cutoff answers.
Delivery lateness is defined only for delivered orders with valid estimated and
actual timestamps. See [`phase3c_methodology.md`](phase3c_methodology.md) for
full definitions. These operational estimates are observational associations,
not causal effects.
