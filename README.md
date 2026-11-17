# Business Rating Reliability & Decision Intelligence

## Problem statement

Simple average-star rankings treat a business with a handful of reviews like a
business with hundreds of reviews. That can make rankings unstable and can hide
meaningful differences in the amount and timing of evidence behind each score.
This independent portfolio project asks how statistical methods can improve the
reliability and interpretability of business ratings without overstating what
the available data can support.

## Motivation

The project was motivated by prior part-time work as a Software Quality Analyst
reviewing customer feedback and evaluating businesses. It is an independent
research and portfolio project; it is not a product built for, sponsored by, or
deployed for that employer.

## Research objectives

- Describe rating distributions, review volume, and changes over time.
- Compare observed arithmetic means with empirical-Bayes-style shrinkage
  estimates.
- Quantify uncertainty separately from adjusted scores.
- Investigate how sensitive business rankings are to new data and modeling
  choices.
- Evaluate candidate methods on historical review data against transparent
  baselines.
- Communicate useful conclusions and limitations through a report and Power BI
  dashboards in later phases.

The primary research question and scoped subquestions are documented in
[`docs/research_questions.md`](docs/research_questions.md).

## Technology stack

- Python 3.12 or 3.13, with pandas, NumPy, SciPy, and statsmodels introduced only
  when later phases require them
- DuckDB for local validated storage and analytical SQL
- pytest for tests and Ruff for linting and formatting
- Jupyter for exploratory analysis
- Power BI for later interactive reporting
- Git and GitHub for version control

Phase 2A adds DuckDB as the only runtime dependency. Statistical libraries are
still deferred until the research phase needs them.

## Planned methodology

The study will begin with data-quality checks and descriptive analysis, then
establish the arithmetic mean as a baseline. A shrinkage estimator will combine
each business's observed mean with a clearly defined reference mean. Later work
will estimate statistical uncertainty, measure ranking stability, and perform
time-aware historical evaluation. These concepts answer different questions and
will not be treated as interchangeable. See
[`docs/methodology.md`](docs/methodology.md) for the current methodological
contract.

No real results are reported yet.

## Project structure

```text
business-rating-reliability/
|-- data/
|   |-- raw/                 # Local source data; never committed
|   |-- interim/             # Local intermediate data; never committed
|   |-- processed/           # Local derived data; never committed
|   `-- sample/              # Approved synthetic samples only
|-- dashboards/powerbi/      # Power BI work in a later phase
|-- docs/                    # Research and design documentation
|-- notebooks/               # Exploratory notebooks in a later phase
|-- reports/figures/         # Reproducible report figures
|-- sql/analysis/            # Version-controlled analytical SQL
|-- src/rating_reliability/  # Python package and ingestion modules
|-- tests/                   # Synthetic-data tests
|-- pyproject.toml           # Package and tool configuration
`-- README.md
```

## Local setup

The package supports Python 3.12 and 3.13. Install the editable project into the
existing Windows environment from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

For a new environment, create `.venv` with a compatible interpreter first. The
commands use the environment's Python directly, so activation is not required.
Do not put secrets in `.env.example`.

## Phase 2A ingestion architecture

The ingestion command reads UTF-8 business and review JSONL files one physical
line at a time. Validated candidates are inserted into temporary DuckDB tables
in bounded batches. DuckDB window functions then apply deterministic
first-valid-occurrence deduplication, and a SQL join excludes reviews whose
business was not accepted. Only the final `businesses` and `reviews` tables are
published.

The implementation deliberately keeps individual review stars and dates. It
does not calculate rankings, remove valid low or high ratings, impose a minimum
review count, or assume the observed reviews represent every customer.

### Cleaning rules

- Required identifiers are trimmed and must be nonempty strings.
- Optional business text is trimmed; missing, blank, or incorrectly typed values
  become `NULL` with a warning.
- Valid source aggregate stars are finite numbers from 1 through 5. Valid source
  review counts are nonnegative integers. Invalid or missing optional aggregates
  become `NULL` with a warning rather than rejecting the business.
- Review stars must be JSON integers from 1 through 5. Booleans, floats, strings,
  nonfinite values, and out-of-range values are rejected.
- Review dates must be exactly `YYYY-MM-DD` or Yelp's expected
  `YYYY-MM-DD HH:MM:SS` form. The table stores only the calendar `DATE`; no
  timezone or time is invented.
- Among schema-valid records with the same identifier, the earliest source line
  wins. Later identical and conflicting occurrences are separately counted and
  rejected. Conflicts never overwrite the first record.
- A first-occurrence review that references no accepted business is counted as
  orphaned and excluded from `reviews` through a database join.

Source aggregate fields remain named `source_stars` and `source_review_count`.
They may use a different population or cutoff from the supplied review file and
are never substituted for metrics calculated from accepted reviews.

### Data-quality accounting

`total_lines` includes blank lines. `processed_records` is the number of nonblank
physical lines and is the denominator for acceptance and rejection rates. Every
processed record is classified exactly once as accepted or rejected. Specific
issue counters can overlap, so they must not be summed to infer rejected record
count. Blank lines are reported but excluded from record-level rates. The JSON
report contains counts and reason codes only; it never copies review text.

### Run the synthetic pipeline

The tracked inputs are fictional and intentionally include quality problems.
Outputs belong in the Git-ignored `data/processed/` directory:

```powershell
.\.venv\Scripts\python.exe -m rating_reliability.ingest `
  --businesses data\sample\synthetic_businesses.jsonl `
  --reviews data\sample\synthetic_reviews.jsonl `
  --database data\processed\rating_reliability.duckdb `
  --quality-report data\processed\quality_report.json `
  --batch-size 2
```

The command refuses to replace either output unless `--overwrite` is provided.
It builds the database and report at temporary sibling paths first, preserving
existing outputs if parsing, validation, loading, or relational finalization
fails. Windows cannot atomically replace two separate files as one operation;
the database is replaced first after both staged outputs are complete.

Inspect the local database without adding another command-line dependency:

```powershell
@'
import duckdb
with duckdb.connect("data/processed/rating_reliability.duckdb", read_only=True) as con:
    print(con.execute("SHOW TABLES").fetchall())
    print(con.execute("SELECT count(*) FROM businesses").fetchone())
    print(con.execute("SELECT count(*) FROM reviews").fetchone())
'@ | .\.venv\Scripts\python.exe -
```

## Testing and quality checks

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

To apply Ruff's safe formatting:

```powershell
.\.venv\Scripts\python.exe -m ruff format .
```

## Current development status

**Phase 2A - synthetic ingestion foundation.** Incremental JSONL parsing,
validation, deterministic deduplication, orphan exclusion, DuckDB storage,
quality reporting, overwrite protection, a CLI, and synthetic automated tests
are implemented. No real Yelp records have been ingested. Exploratory analysis,
analytical-population selection, uncertainty estimation, ranking comparison,
historical evaluation, dashboards, and research findings remain unimplemented.

## Known limitations and Phase 2B boundary

Phase 2A recognizes the expected source fields but has not verified them against
a locally obtained Yelp dataset version. The pipeline retains the first valid
duplicate rather than trying to infer which conflicting record is authoritative.
Date-time source values are intentionally reduced to dates. Quality reports
contain aggregate reason counts rather than every rejected source line. The
pipeline is single-process and local by design.

Phase 2B will begin only after the dataset is manually obtained under its terms.
It will verify the real schema and version, measure observed quality and coverage,
define a defensible analytical population, investigate selection concerns, and
prepare accepted data for statistical analysis. It will not assume synthetic
quality rates describe the real dataset.

## Dataset attribution and usage warning

The planned source is the [Yelp Open Dataset](https://business.yelp.com/data/resources/open-dataset/).
Users must obtain it themselves and comply with Yelp's current dataset terms and
academic-use restrictions. This repository must not download or scrape Yelp,
and raw Yelp data, private records, large exports, and real review text must not
be committed. Initial examples and tests use synthetic data only. The dataset is
not included in this repository.

