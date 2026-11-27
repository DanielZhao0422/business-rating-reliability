# Data Dictionary

This document separates the implemented Phase 2A synthetic schema, implemented
Phase 2C Olist schema, implemented local Phase 3 outputs, and proposed Phase 4B
Power BI export schema.

## Implemented Phase 2A synthetic schema

The existing JSONL pipeline implements these canonical tables against fictional
Yelp-style fixtures. It remains tested infrastructure but is not the Olist
analytical schema.

### `businesses` -- implemented for synthetic JSONL

| Column | Source field | DuckDB type | Null? | Meaning |
|---|---|---|---:|---|
| `business_id` | `business_id` | `VARCHAR` primary key | No | Trimmed nonempty identifier |
| `name` | `name` | `VARCHAR` | Yes | Optional source name |
| `city` | `city` | `VARCHAR` | Yes | Optional source city |
| `state` | `state` | `VARCHAR` | Yes | Optional source state |
| `categories` | `categories` | `VARCHAR` | Yes | Optional source categories |
| `source_stars` | `stars` | `DOUBLE` | Yes | Valid 1--5 source aggregate |
| `source_review_count` | `review_count` | `BIGINT` | Yes | Nonnegative source aggregate count |

### `reviews` -- implemented for synthetic JSONL

| Column | Source field | DuckDB type | Null? | Meaning |
|---|---|---|---:|---|
| `review_id` | `review_id` | `VARCHAR` primary key | No | Trimmed nonempty identifier |
| `business_id` | `business_id` | `VARCHAR`, foreign key | No | Accepted business relationship |
| `stars` | `stars` | `TINYINT` | No | Integer 1 through 5 |
| `date` | `date` | `DATE` | No | Valid calendar review date |

Review text is never selected or stored. Source aggregate values remain distinct
from reconstructed metrics because populations, cutoffs, rounding, or source
rules may differ.

## Olist source fields used by the audit

Olist reviews are order-level. The audit reads the original CSVs directly and
derives a temporary `usable_ratings` view without changing source contents.

| Analytical concept | Olist source | Meaning |
|---|---|---|
| Order observation | reviews/orders/items `order_id` | Analytical observation key |
| Source review identity | reviews `review_id` | Not globally unique; used for ambiguity checks |
| Associated seller | items `seller_id` | Sole distinct seller for an eligible order |
| Customer rating | reviews `review_score` | Integer 1--5 order-experience rating |
| Survey creation | reviews `review_creation_date` | Survey send time, retained for traceability |
| Rating availability | reviews `review_answer_timestamp` | Customer answer time used for window assignment |
| Purchase time | orders `order_purchase_timestamp` | Operational context, not rating availability |
| Fulfillment status | orders `order_status` | Operational outcome/context |
| Estimated delivery | orders `order_estimated_delivery_date` | Delivery-timeliness reference |
| Actual delivery | orders `order_delivered_customer_date` | Observed delivery when present |
| Item value | items `price` | Summed or otherwise defined at order level |
| Freight value | items `freight_value` | Summed or otherwise defined at order level |
| Product category | products `product_category_name` | Product-mix context when available |

The primary quantity is a **customer order experience rating associated with a
marketplace seller**. A "seller rating" is a summary of eligible observations,
not an intrinsic seller-quality field.

## Seller-associated observation -- established rule

A usable observation requires exactly one distinct seller, one review row for the
order, a review ID linked to only one order, valid order and seller identifiers,
a numerical 1--5 rating, a valid creation timestamp, and a finite answer
timestamp equal to or later than creation. Multiple items from
the same seller remain eligible. `order_id` is the observation key.

The completed audit measured 95,245 usable observations for 3,064 sellers. Phase
2C independently reproduces those counts in permanent local analytical tables.

## Implemented Phase 2C Olist analytical tables

### `source_sellers` -- implemented

Contains source seller identifiers, optional seller geography, source row number,
and identifier validation. Duplicate source keys remain measurable rather than
being silently overwritten.

### `seller_rating_observations` -- implemented

Fields include:

| Field | Purpose |
|---|---|
| `order_id` | Primary analytical observation key |
| `seller_id` | Sole associated seller |
| `review_id` | Source traceability; not a primary key |
| `rating` | Valid 1--5 order-experience score |
| `review_creation_date` | Survey creation and timestamp validation |
| `review_answer_timestamp` | Rating availability and cutoff assignment |
| `order_purchase_timestamp` | Operational timing |
| `order_status` | Fulfillment context |

The table has a primary key on `order_id`, non-null seller/review/rating/date
fields, and a 1--5 rating constraint.

### `order_eligibility` -- implemented

Contains seller/review counts, overlapping validation flags, one deterministic
primary reason, and final eligibility for each nonblank source order ID.

### `seller_operational_features` -- implemented

Contains one row per eligible order with item/freight totals, item and product
counts, delivery timestamps, whole-day delay, and nullable late-delivery status.

| Field | Meaning |
|---|---|
| `order_id` | Primary key and link to the rating observation |
| `seller_id` | Sole associated seller |
| `order_status` | Source fulfillment status |
| `purchase_timestamp` | Source purchase timestamp |
| `estimated_delivery_timestamp` | Supplied delivery estimate |
| `actual_delivery_timestamp` | Actual delivery when available |
| `delivery_delay_days` | Whole days from estimate to actual delivery |
| `delivered_late` | Null unless both delivery timestamps exist |
| `item_count` | Number of order-item rows |
| `distinct_product_count` | Distinct products in the order |
| `total_item_value` | Sum of valid source item prices |
| `total_freight_value` | Sum of valid source freight values |

### `order_product_categories` -- implemented

Preserves the many-to-many order/category relationship. No single category is
chosen for multi-category orders.

### Historical population views -- implemented

`historical_rating_observations` contains reviews before 2018-01-01;
`evaluation_rating_observations` contains reviews on or after that date.

### Seller-period statistical summaries -- implemented in Phase 3B

Phase 3B derives seller-period counts, raw means, adjusted estimates, uncertainty
measures, and evaluation outcomes in memory. These are analytical outputs, not
source fields, and preserve the 2018-01-01 historical boundary.

The internal historical seller summary has:

| Field | Meaning |
|---|---|
| `seller_id` | Deterministically ordered seller key; never exported in tracked reports |
| `historical_review_count` | Eligible ratings strictly before the cutoff |
| `historical_rating_sum` | Sum of those 1--5 ratings |
| `historical_mean_rating` | Arithmetic historical mean |
| `count_1_star` ... `count_5_star` | Historical category counts that reconcile count and sum |

Local Phase 3B reports under `reports/local/statistical_analysis/` are:

| File | Contents |
|---|---|
| `model_comparison.json` | Reference distribution, selected strength, paired predictive metrics, bootstrap interval |
| `prior_tuning.json` | Fold boundaries, populations, candidate losses, selection and fallback status |
| `uncertainty_summary.json` | Credible/predictive interval methodology, coverage, width, assumptions |
| `ranking_comparison.json` | Tie-aware agreement, top-K overlap, rank changes, posterior stability |
| `evaluation_by_volume.csv` | Aggregate historical/evaluation volume-band metrics |
| `reproducibility.json` | Fingerprints, versions, seeds, population reconciliation |
| `statistical_results.md` | Concise local factual interpretation and limitations |

No output contains review text, customer identifiers, or seller-level rows.

### Local Phase 3C aggregate artifacts

`reports/local/phase3c/` contains scenario-level sensitivity CSVs, tie and
ranking JSON summaries, calibration diagnostics, aggregate operational
associations, population/missingness counts, reproducibility metadata, a local
findings report, and research figures. CSV rows identify scenarios or aggregate
groups; none contains seller or order identifiers. Exact schemas are generated
from the typed Phase 3C modules and documented in the local findings report.

## Missing values and validity

Valid endpoint ratings of 1 and 5 remain included. Required identifiers,
ratings, and review dates cannot be invented. Optional operational values may be
null and should be reported rather than imputed without a defined method. No
universal minimum seller review count is an ingestion rule.

## Local reports and privacy

`schema_report.json`, `dataset_profile.json`, `data_quality_summary.json`,
`population_summary.json`, and the Olist audit artifacts are local analytical
reports. They may contain aggregate counts, inferred types, file sizes, and
SHA-256 fingerprints but no review text or customer identifiers.

All original Olist CSVs, real-data-derived tables, and local reports remain in
Git-ignored paths. Public release remains conditional on the applicable CC
BY-NC-SA 4.0 requirements and underlying-rights review.

## Phase 4B-1 Power BI export schema

Phase 4A defined and Phase 4B-1 implements a curated CSV layer under the ignored
`reports/local/powerbi/` directory. The model includes method, historical
volume, scenario, and pseudonymized seller dimensions; seller estimate,
evaluation, and operational facts; and disconnected aggregate facts for fixed
study metrics, ranking, robustness, calibration, fulfillment status, and
operational associations.

The seller facts never export source seller IDs. A local HMAC-derived
`seller_key` supplies stable joins across files. No order ID, review ID,
customer ID, or review text is included, and no order-level export is required.
Exact grains, columns, keys, relationships, and source mappings are documented
in [`powerbi_data_model.md`](powerbi_data_model.md) and
`config/powerbi_export_spec.toml`.

The exporter reconstructs seller-grain locked-model results in memory, replaces
source IDs with 128-bit hexadecimal HMAC-SHA256 pseudonyms, validates an exact
allowlist, and never writes a reverse mapping. Blank CSV cells mean missing or
not applicable; zero remains numeric zero. Files use UTF-8 with BOM, CRLF,
invariant decimal points, and ISO dates. Metadata records hashes and row counts,
not source identifiers or the secret.
