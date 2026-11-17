# Data Dictionary

No Yelp data is included or ingested in Phase 1. This provisional dictionary
records the minimum analytical concepts expected in later phases; it is not a
claim about a finalized schema. Field names and types must be verified against
the downloaded dataset documentation before implementation.

| Analytical field | Intended meaning | Expected type | Phase 1 status |
|---|---|---:|---|
| `business_id` | Dataset-provided business identifier | string | Planned; potentially sensitive linkage key |
| `review_id` | Dataset-provided review identifier | string | Planned; never used to commit real text |
| `stars` | Review rating on the 1--5 scale | numeric | Represented only by synthetic test values |
| `review_date` | Review event date/time | date or timestamp | Planned; needed for time-aware evaluation |
| `business_category` | Business classification | string or list | Planned; requires normalization decisions |
| `city` / `state` | Coarse business geography | string | Planned; coverage and privacy require review |
| `observed_review_count` | Number of eligible reviews in a defined window | integer | Derived later |
| `observed_mean_rating` | Arithmetic mean within a defined window | float | Baseline function implemented |
| `adjusted_mean_rating` | Shrinkage point estimate using documented inputs | float | Baseline function implemented |

Real review text must not be committed. Raw, interim, and processed datasets
remain local and Git-ignored. Only explicitly approved synthetic fixtures may be
placed in `data/sample/`.

