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

