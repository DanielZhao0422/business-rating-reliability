# Analytical Population Design

## Purpose

Population design is separate from technical cleaning. This prevents convenient
filters from silently changing the research question and preserves low-review-
count sellers, who are central to rating-reliability analysis.

## Population stages

### 1. Source population

The source population is the local Olist release before analytical exclusions:
99,441 orders, 99,224 review rows, 112,650 order-item rows, and 3,095 seller
records. The raw CSVs remain local and Git-ignored. These records represent the
available Olist data, not all marketplace transactions, customers, or sellers.

### 2. Validated source population -- implemented in Phase 2C

Privacy-safe `source_*` DuckDB tables preserve required identifiers, parsed
timestamps and values, and validation flags. The database omits customer IDs,
review text, payment details, and raw geolocation. Phase 2A's Yelp-style
synthetic JSONL tables remain separate working infrastructure rather than being
repurposed as the Olist schema.

### 3. Seller-attributable population -- rule established by audit

A usable seller-associated rating observation requires:

1. an order containing exactly one distinct seller;
2. exactly one review row associated with that order;
3. a review identifier associated with only one order;
4. valid order and seller identifiers;
5. a valid numerical rating from 1 through 5; and
6. a valid survey creation timestamp and a finite answer timestamp equal to or
   later than creation.

The order ID is the analytical observation key. `review_id` is not globally
unique. Multiple items supplied by the same seller still constitute a single-
seller order. The audit measured 95,245 usable observations representing 3,064
sellers; Phase 2C independently reproduced those counts in
`seller_rating_observations`.

Orders with multiple sellers, ambiguous review identity, or invalid required
values are excluded rather than resolved arbitrarily. Exclusion counts must
remain visible in population-flow reporting.

### 4. Descriptive population

All 95,245 usable seller-associated rating observations and all 3,064 represented
sellers are eligible for broad description. Sellers with one review remain
included. No universal threshold based on rating level, review volume, product
category, geography, or operational outcome is applied.

Review-volume bands may be used to describe heterogeneity, but they are not
cleaning rules. The audit found a median of 6 and a maximum of 1,786 usable
ratings per seller.

### 5. Statistical comparison populations -- finalized for Phase 3

Raw and adjusted historical point estimates require at least one historical
observation. The full descriptive population retains every eligible seller.
The primary final evaluation and ranking universe requires at least one
historical and one later rating. Requiring the later outcome is retrospective
evaluation eligibility, not prediction-time information.

Internal chronological validation requires, per fold, at least one rating in
that fold's earlier training window and one in its later validation window.
Uncertainty intervals require at least one historical rating; empirical
calibration additionally requires a later outcome. Operational analysis requires
a historical estimate and eligible later orders with the outcome-specific field.

Historical minimums 5, 10, and 20 and evaluation minimums 5 and 10 are
prespecified sensitivities, not cleaning rules. Every analysis reports included
sellers, ratings/orders, missing outcomes, and the relevant source denominator.
The complete definitions and selection risks are tabulated in
[`docs/statistical_analysis_plan.md`](statistical_analysis_plan.md).

### 6. Historical evaluation population -- fixed primary design

The primary cutoff is **2018-01-01**:

- historical observations: reviews answered before 2018-01-01;
- evaluation observations: reviews answered on or after 2018-01-01.

The creation-date audit measured 40,976 historical ratings, 54,269 later
ratings and 1,094 paired sellers. The [availability amendment](temporal_availability_amendment.md)
measured 40,321 historical ratings, 54,924 later ratings and 1,098 paired
sellers. Both runs had 429 sellers with at least 10 ratings in each period. The cutoff was chosen before model comparison because it has a
clear calendar interpretation and adequate two-period coverage, not because it
produced favorable results.

Historical estimates, pooled reference means, prior strengths, hyperparameters,
and eligibility decisions must use only pre-cutoff information. Later ratings
are out-of-sample outcomes, not objective ground truth for seller quality. Other
cutoffs may be reported as sensitivity analyses without replacing the primary
design after results are observed.

## Required population-flow reporting

Future analytical outputs must reconcile these stages:

```text
source orders and review rows
  -> technically valid source records
  -> unambiguous single-seller order observations
  -> descriptive seller population
  -> method-specific statistical population
  -> historical/evaluation population at 2018-01-01
```

For each transition, report included and excluded orders, rating observations,
and sellers, with non-overlapping primary exclusion reasons where practical.
Phase 2C materializes these decisions in `order_eligibility` and writes aggregate
population reports under `reports/local/olist_preparation/`.

## Selection and representation risks

- **Attribution selection:** multi-seller orders and ambiguous reviews are not a
  random sample; the audit found operational differences by attribution group.
- **Review participation:** customers who leave ratings may differ from those
  who do not. Missing ratings are not assumed positive or negative.
- **Marketplace selection:** Olist sellers and orders do not represent every
  Brazilian marketplace seller or transaction.
- **Survivorship:** sellers active long enough to enter both periods may differ
  systematically from other sellers.
- **Category and geographic imbalance:** seller mix, rating behavior, delivery,
  and review volume may differ by product or location.
- **Time-related bias:** platform activity, logistics, seller composition, and
  customer behavior can change between 2016 and 2018.
- **Outcome ambiguity:** an order-experience rating can reflect product,
  logistics, marketplace, and seller-related factors.

Large sample size does not remove these limitations. The statistical study must
describe the population it supports rather than generalize to intrinsic seller
quality.

## Phase 3B implemented populations

The software now materializes these populations in memory without changing the
Phase 2C database:

- historical estimation: all sellers with at least one eligible pre-cutoff
  rating;
- fold validation: the intersection of sellers with earlier fold training data
  and later observations inside that fold;
- final paired evaluation and ranking: the intersection of historical and
  evaluation sellers, identically applied to both methods;
- expected-rating uncertainty: all historical sellers;
- predictive calibration: paired evaluation sellers, conditional on each
  realized later rating count.

Every local report records its seller and rating denominators. Sellers that
appear only after the cutoff receive no fabricated historical score. Volume
bands remain reporting groups rather than data-cleaning thresholds.

## Phase 3C populations

Sensitivity analyses retain the paired Phase 3B universe except where a stated
historical or retrospective future-count threshold changes the denominator.
Alternative cutoffs rebuild both periods and report their own seller and rating
counts; their metrics are not treated as comparisons over identical populations.

The operational cohort is one row per eligible reviewed order purchased on or
after 2018-01-01, reviewed on or after that date, and attributed to a seller with
a pre-cutoff estimate. Lateness uses the subset marked delivered with both
delivery timestamps. Non-delivered statuses remain a separate fulfillment
distribution, and missing delivered timing is bounded rather than imputed.
