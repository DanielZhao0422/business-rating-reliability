# Methodology

This document defines the analytical concepts before data analysis begins. They
are related, but they are not interchangeable measures.

## Observed average ratings

The observed average is the arithmetic mean of a business's valid 1--5 ratings.
It describes the center of the reviews currently observed. It does not account
for how many reviews produced the mean, sampling or selection bias, time trends,
or uncertainty. It is the primary transparent baseline.

## Adjusted ratings

The Phase 1 adjusted rating is

\[
\frac{n\bar{x} + m\mu}{n + m},
\]

where \(n\) is the observed review count, \(\bar{x}\) is the observed mean,
\(\mu\) is a documented reference mean, and \(m > 0\) is the prior strength.
This is an empirical-Bayes-style shrinkage estimator: small samples move more
toward the reference, while large samples remain closer to their observed mean.
The choices of reference population and prior strength require justification and
sensitivity analysis. An adjusted rating is a point estimate, not an uncertainty
interval.

## Statistical uncertainty

Statistical uncertainty describes the range of values compatible with the data
and stated modeling assumptions. A later phase may use confidence intervals,
posterior intervals, or bootstrap intervals after their assumptions are made
explicit. Interval width and coverage answer different questions from the
adjusted point score. Reviews may also be non-independent and non-representative,
so a narrow model-based interval does not remove selection bias.

## Business ranking stability

Ranking stability concerns whether relative positions persist when reviews are
added, data are resampled, time windows change, or reasonable modeling choices
change. It can be studied through rank correlations, top-k membership changes,
or rank distributions. A stable ranking is not automatically accurate or fair,
and a point estimate with a high value is not automatically stable.

## Predictive or historical evaluation

Evaluation asks whether a method performs well on data that were not used to
construct its score. Reviews should be split in time order to avoid using future
information. Candidate methods will be compared with simple, documented
baselines using preselected outcomes and scoring rules. Evaluation may assess
future aggregate ratings, calibration, error, or ranking behavior; each target
must be defined before interpreting results. Predictive success does not by
itself establish a causal explanation.

## Initial assumptions and limitations

- Ratings are validated on an inclusive 1--5 numeric scale.
- Phase 1 gives each observed rating equal weight and does not model time.
- The reference mean and prior strength are inputs, not facts learned in Phase 1.
- Yelp reviews are observational and may reflect selection, platform, survivorship,
  geographic, and temporal biases.
- Missingness, duplicate records, business closures, category definitions, and
  changes in platform behavior require investigation before substantive claims.

## Phase 2A ingestion population

Ingestion establishes a technically valid relational population; it does not
claim that population is statistically representative. A review enters the
analytical `reviews` table only when its required fields are valid, it is the
first valid occurrence of its review identifier, and it references an accepted
business. Valid ratings at both endpoints are retained. There is no outlier rule
or minimum-volume threshold.

Optional business values are allowed to be missing because rejecting an entire
business for absent geography or categories could create unnecessary selection.
Invalid optional source aggregates become `NULL` with warnings. Required review
values are rejected because an unidentified review, an invalid rating, or an
unknown date cannot support the planned rating and temporal analyses.

## Duplicate and relationship policy

Duplicate detection operates on normalized, schema-valid candidates and remains
correct across processing batches. The earliest valid source line for an
identifier is retained. Later identical occurrences are redundant; later
conflicting occurrences are conservatively rejected rather than overwriting the
first record. Both are counted separately. This deterministic rule avoids double
counting but does not claim the first conflicting record is substantively true.

Reviews referencing no accepted business are labeled orphaned and excluded by a
DuckDB join. This preserves referential integrity and prevents reviews from
entering business-level calculations without a corresponding analytical entity.
Orphan counts remain visible in the quality report.

## Quality-count interpretation

Blank lines are counted but excluded from record denominators. Every nonblank
line is exactly one accepted or rejected record attempt, so these totals
reconcile. A record may violate several schema rules; reason-code counters may
therefore overlap and must not be summed as a rejected-record total. Synthetic
fixture rates test the implementation and must not be presented as Yelp research
results.

## Separation from statistical analysis

Phase 2A does not rank businesses, calculate review-derived averages, estimate
uncertainty, or compare methods. Source aggregates are stored separately rather
than treated as ground truth. Later research must define its analytical review
population, examine selection and coverage, and compute metrics from the reviews
actually included under that definition.

