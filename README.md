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

- Python 3.12 with pandas, NumPy, SciPy, and statsmodels introduced only when
  later phases require them
- DuckDB initially, with PostgreSQL considered for later workflow expansion
- pytest for tests and Ruff for linting and formatting
- Jupyter for exploratory analysis
- Power BI for later interactive reporting
- Git and GitHub for version control

Phase 1 deliberately uses only the Python standard library at runtime.

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
|-- src/rating_reliability/  # Python package
|-- tests/                   # Synthetic-data unit tests
|-- pyproject.toml           # Package and tool configuration
`-- README.md
```

## Local setup

Python 3.12 is the preferred interpreter. From PowerShell in the repository
root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell blocks activation, the virtual environment's executables can be
called directly (for example, `.\.venv\Scripts\python.exe`). Do not put secrets
in `.env.example`; copy it to an ignored `.env` for local values when later
phases introduce configuration.

## Testing and quality checks

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

To apply Ruff's safe formatting:

```powershell
python -m ruff format .
```

## Current development status

**Phase 1 — project initialization.** The repository structure, documentation,
tool configuration, and two small baseline rating functions are present. Data
ingestion, exploratory analysis, uncertainty estimation, historical evaluation,
dashboards, and research findings have not been implemented.

## Dataset attribution and usage warning

The planned source is the [Yelp Open Dataset](https://business.yelp.com/data/resources/open-dataset/).
Users must obtain it themselves and comply with Yelp's current dataset terms and
academic-use restrictions. This repository must not download or scrape Yelp,
and raw Yelp data, private records, large exports, and real review text must not
be committed. Initial examples and tests use synthetic data only. The dataset is
not included in this repository.

