# Data Dictionary

Phase 2A implements the following schema against fictional Yelp-style fixtures.
The expected source mappings must still be checked against the specific real
dataset version in Phase 2B.

## `businesses`

| Column | Expected source field | DuckDB type | Null? | Validation and meaning |
|---|---|---|---:|---|
| `business_id` | `business_id` | `VARCHAR` primary key | No | Trimmed, nonempty string; required |
| `name` | `name` | `VARCHAR` | Yes | Trimmed optional text; no invented replacement |
| `city` | `city` | `VARCHAR` | Yes | Trimmed optional text |
| `state` | `state` | `VARCHAR` | Yes | Trimmed optional text |
| `categories` | `categories` | `VARCHAR` | Yes | Source category string preserved after outer trimming |
| `source_stars` | `stars` | `DOUBLE` | Yes | Finite 1-5 source aggregate; invalid or missing becomes `NULL` plus warning |
| `source_review_count` | `review_count` | `BIGINT` | Yes | Nonnegative integer source aggregate; invalid or missing becomes `NULL` plus warning |

## `reviews`

| Column | Expected source field | DuckDB type | Null? | Validation and meaning |
|---|---|---|---:|---|
| `review_id` | `review_id` | `VARCHAR` primary key | No | Trimmed, nonempty string; required |
| `business_id` | `business_id` | `VARCHAR`, foreign key | No | Must reference an accepted business |
| `stars` | `stars` | `TINYINT` | No | JSON integer from 1 through 5; booleans and numeric strings rejected |
| `date` | `date` | `DATE` | No | Valid date parsed from `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS` |

Review text is intentionally not selected or stored. Individual ratings and
dates remain unaggregated so later work can study distributions, review volume,
time, uncertainty, ranking stability, and temporal evaluation.

## Missing values and normalization

Outer whitespace is removed from valid identifiers and text. Required review
fields are fatal when absent or invalid. Optional business fields are permitted;
missing or invalid optional values become SQL `NULL` and create warning reason
codes. The pipeline never fills missing geography, categories, ratings, dates,
or identifiers with guesses.

Valid review ratings of 1 and 5 are retained. No rating is removed merely for
being unusual, and no review-count threshold is applied during ingestion.

## Source aggregates versus analytical metrics

`source_stars` and `source_review_count` are descriptive fields supplied on the
business record. They may reflect a different review population or cutoff than
the review JSONL file. A mismatch with later reconstructed metrics is not, by
itself, evidence of corruption. Primary research metrics will be computed from
the explicitly selected review population.

Real review text and records must not be committed. Raw, interim, and processed
data remain local and Git-ignored. Only the named fictional JSONL fixtures in
`data/sample/` are approved for version control.

