# Olist Analytical Data Integration

## Phase 2C status

Phase 2C is implemented and verified against the local Olist CSV release. The
pipeline creates a privacy-safe DuckDB analytical database, explicit order
eligibility decisions, historical/evaluation views, operational features, and
aggregate local reports. It does not calculate adjusted ratings, uncertainty,
seller rankings, or Phase 3 evaluation results.

## Reproduction

```powershell
.\.venv\Scripts\python.exe -m rating_reliability.olist_prepare `
  --data-dir data\raw\olist `
  --database data\processed\olist_analytics.duckdb `
  --output-dir reports\local\olist_preparation `
  --overwrite
```

Both destination paths are Git-ignored. Without `--overwrite`, the command
refuses to replace either output. It builds a staged database and report
directory and publishes them only after transformations and integrity checks
succeed. A validation failure leaves the previous outputs unchanged.

## Source scope and privacy

The pipeline reads orders, order items, reviews, sellers, and products. It stores
only fields needed for rating reliability, attribution, time ordering, and
operational analysis. It deliberately excludes customer identifiers, review
text, payment details, and raw geolocation.

Source CSVs are read as text first. Required identifiers and numerical or
timestamp fields are parsed with explicit validation flags; invalid values are
not silently converted into valid observations. Optional delivery timestamps
may remain null without excluding an otherwise valid rating.

## Analytical database

### Privacy-safe source tables

- `source_orders`: order status and operational timestamps, without customer ID.
- `source_order_items`: order-item keys, seller/product relationships, price,
  freight, and validation flags.
- `source_order_reviews`: review/order identity, 1--5 rating, survey creation and
  answer timestamps, and validation flags; no title or message.
- `source_sellers`: seller identifier and optional seller geography.
- `source_products`: product identifier and source category.
- `source_validation_summary`: aggregate key, type, range, and relationship
  diagnostics.

### Eligibility and analytical tables

- `order_eligibility`: one row per nonblank source order ID, overlapping issue
  flags, one deterministic primary reason, seller/review counts, and final
  eligibility.
- `seller_rating_observations`: one constrained row per eligible `order_id`, with
  seller, source review ID, rating, survey creation and answer timestamps, purchase
  timestamp, and order status.
- `seller_operational_features`: one row per eligible order with item/freight
  totals, item/product counts, delivery timestamps, delay days, and late flag.
- `order_product_categories`: a many-to-many order/category relationship. No
  arbitrary category is selected for a multi-category order.
- `analytical_population_summary`: materialized population-stage counts.
- `analytical_quality_checks`: persistent zero-failure invariant results.
- `pipeline_metadata`: versions, cutoff, timestamp, and input fingerprints.

### Historical views

- `historical_rating_observations`: `review_answer_timestamp < 2018-01-01`.
- `evaluation_rating_observations`: `review_answer_timestamp >= 2018-01-01`.

Purchase timestamps do not determine rating availability. Phase 3 priors,
hyperparameters, eligibility, and estimates must use historical data only.

## Eligibility policy

An eligible observation requires one distinct seller, exactly one review row, a
review ID linked to one order, valid order/seller identifiers, an integer 1--5
rating, a valid survey creation timestamp and a finite answer timestamp equal
to or later than creation. `order_id` is the primary analytical
key. `review_id` is retained for traceability but is not globally unique.

Overlapping flags preserve every observed issue. The primary reason uses this
deterministic precedence:

1. `DUPLICATE_ORDER_ID`
2. `NO_ORDER_ITEMS`
3. `DUPLICATE_ORDER_ITEM_KEY`
4. `INVALID_SELLER_ID`
5. `MULTI_SELLER_ORDER`
6. `NO_REVIEW`
7. `MULTIPLE_REVIEWS`
8. `INVALID_REVIEW_ID`
9. `NON_UNIQUE_REVIEW_ID`
10. `INVALID_RATING`
11. `INVALID_REVIEW_DATE`
12. `MISSING_REVIEW_ANSWER_TIMESTAMP`
13. `INVALID_REVIEW_ANSWER_TIMESTAMP`
14. `ANSWER_BEFORE_REVIEW_CREATION`
15. `ELIGIBLE`

Primary counts are mutually exclusive. Issue-flag counts overlap and must not be
summed. Rows with a blank order ID cannot enter an order-keyed table and remain
visible in source validation.

## Real-data reconciliation

| Measure | Phase 2C result |
|---|---:|
| Source orders | 99,441 |
| Source order items | 112,650 |
| Source reviews | 99,224 |
| Source sellers | 3,095 |
| Eligible rating observations | 95,245 |
| Distinct rated sellers | 3,064 |
| Historical observations | 40,321 |
| Evaluation observations | 54,924 |
| Sellers in both periods | 1,098 |
| Sellers with at least 10 in each period | 429 |

Attribution counts reconcile to the Phase 2B audit. The period counts use
answer timestamps and supersede the audit's creation-date split.
[Temporal availability amendment](temporal_availability_amendment.md)

Primary order outcomes are:

| Primary reason | Orders |
|---|---:|
| `ELIGIBLE` | 95,245 |
| `NO_ORDER_ITEMS` | 775 |
| `NO_REVIEW` | 735 |
| `MULTI_SELLER_ORDER` | 1,278 |
| `MULTIPLE_REVIEWS` | 531 |
| `NON_UNIQUE_REVIEW_ID` | 877 |

These sum to all 99,441 source orders. Overlapping flags include 768 orders with
no review, 547 with multiple review rows, and 1,412 affected by a non-unique
review ID. Differences from primary counts result from reason precedence, not
unreported deletion.

## Operational features and availability

`total_item_value` and `total_freight_value` are summed once per order before any
category relationship is joined. `delivery_delay_days` is
`actual_delivery_timestamp - estimated_delivery_timestamp` in whole calendar
days; positive values mean late delivery. `delivered_late` is null unless both
timestamps exist, so missing delivery is never classified as on time.

For the 95,245 eligible observations:

- 93,206 have an actual delivery timestamp and computable lateness;
- all 95,245 have an estimated delivery timestamp;
- 7,528 are classified as delivered late;
- 93,886 have at least one known product category; and
- all eligible orders have valid item and freight values.

Delivery and fulfillment fields may become known after purchase or after the
rating estimate's prediction time. They must not be used as historical
predictors before their actual availability.

## Local reports

`reports/local/olist_preparation/` contains:

- `metadata.json`
- `quality_report.json`
- `population_report.json`
- `preparation_summary.json`
- `preparation_summary.md`

Metadata records the source, published license, processing time, input file
names/sizes/SHA-256 fingerprints, application and policy versions, cutoff,
parameters, and privacy exclusions.

## Known limitations

- A single-seller order rating is still an order-experience measure, not direct
  proof of seller responsibility or quality.
- Multi-seller and ambiguous-review exclusions may be systematically different
  from eligible orders.
- Product categories are source labels and may be missing or heterogeneous.
- Day-level delivery delay simplifies timestamp differences and does not model
  business calendars or promised time windows beyond the supplied estimate.
- Local processing permission does not establish unrestricted publication.
  CC BY-NC-SA 4.0 requirements and underlying rights remain documented gates.
