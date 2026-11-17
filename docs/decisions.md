# Architecture Decision Log

## ADR-001: Keep a single repository rooted in the project folder

**Decision:** All source, tests, documentation, SQL, and later analytical assets
live in this standalone repository.

**Rationale:** A single root keeps setup reproducible and prevents accidental
coupling to unrelated Desktop projects.

## ADR-002: Use a `src` package layout

**Decision:** Importable code lives in `src/rating_reliability/`; tests live
outside the package.

**Rationale:** The layout reduces accidental imports from the repository root and
makes editable-install and packaged behavior more consistent.

## ADR-003: Use `pyproject.toml` as the configuration source

**Decision:** Package metadata, dependencies, pytest settings, and Ruff settings
are centralized in `pyproject.toml`.

**Rationale:** This follows modern Python conventions and avoids fragmented tool
configuration. DuckDB is the only Phase 2A runtime dependency.

## ADR-004: Establish small transparent baselines first

**Decision:** Phase 1 implements only the arithmetic mean and a documented
reference-mean shrinkage estimator.

**Rationale:** More complex models need a correct, interpretable comparison point.
The adjusted score is explicitly not presented as an uncertainty interval.

## ADR-005: Separate data zones and keep real data out of Git

**Decision:** Local data will move through `raw`, `interim`, and `processed`
zones, all ignored except placeholder files. The `sample` zone is reserved for
approved synthetic material.

**Rationale:** The separation supports reproducible transformations while
reducing the risk of publishing Yelp data, real review text, private records, or
large generated exports.

## ADR-006: Use DuckDB before PostgreSQL

**Decision:** Phase 2A stores validated local tables in DuckDB. PostgreSQL will
be considered only if collaboration, services, or scale later justify it.

**Rationale:** DuckDB provides a low-operations analytical workflow appropriate
for a portfolio study without premature infrastructure.

## ADR-007: Make evaluation time-aware

**Decision:** Later validation will preserve temporal order and compare methods
against explicit baselines.

**Rationale:** Randomly mixing past and future reviews can leak future information
and exaggerate performance. Exact targets and metrics will be decided before
modeling.

## ADR-008: Target Python 3.12 while allowing local 3.13 verification

**Decision:** `.python-version` records Python 3.12 as the reproducibility target;
package metadata supports Python 3.12 and 3.13.

**Rationale:** Python 3.12 is the requested project standard. Allowing 3.13 makes
the standard-library-only Phase 1 verifiable on the currently available local
interpreter without changing the intended target.

## ADR-009: Stream JSONL and write bounded batches

**Decision:** Input files are read one UTF-8 line at a time, and only a configured
batch of validated candidates is held before a parameterized DuckDB insert.

**Rationale:** JSONL has one independent JSON value per line, so incremental
processing scales without loading the complete Yelp files into Python memory.
The approach remains simple enough to explain and test.

## ADR-010: Separate fatal validation from optional warnings

**Decision:** Invalid identifiers, review ratings, and review dates reject a
record. Missing or invalid optional business values produce warnings and `NULL`.

**Rationale:** Required review fields are necessary for linkage and later
statistical analysis. Rejecting a business only because optional geography,
categories, or source aggregates are absent would discard otherwise useful data
and could create undocumented selection.

## ADR-011: Retain the first valid duplicate deterministically

**Decision:** Among normalized schema-valid candidates, the lowest source line
for an identifier is retained. Later identical and conflicting occurrences are
rejected and counted separately.

**Rationale:** No duplicate can contribute twice, and conflicting records never
silently overwrite one another. First occurrence is reproducible, although it is
not claimed to be more truthful; conflicts remain a quality diagnostic.

## ADR-012: Exclude orphaned reviews through SQL

**Decision:** A review whose business is absent from the accepted business table
is counted as orphaned and excluded from the final review table using a join.

**Rationale:** Business-level analyses require a valid business relationship.
Resolving membership in DuckDB avoids loading every business identifier into a
Python set as the dataset grows.

## ADR-013: Stage complete outputs before replacement

**Decision:** The pipeline builds a temporary sibling database and JSON report.
Existing outputs require `--overwrite` and are not changed until ingestion and
relational validation succeed.

**Rationale:** A failed full refresh should not destroy the last successful
database. Same-directory `os.replace` provides atomic single-file replacement on
supported Windows filesystems. Replacing the database and report together cannot
be one cross-file atomic operation; the database is published first after both
staged files are complete.

## ADR-014: Keep source aggregates distinct

**Decision:** Business-source stars and counts are stored as `source_stars` and
`source_review_count`; review-derived metrics are not calculated during Phase 2A.

**Rationale:** Source aggregates may use a different population or cutoff. Later
analysis must calculate primary metrics from its declared review population and
must not interpret a difference as automatic proof of corruption.

## ADR-015: Delay real-data claims and statistical analysis

**Decision:** Phase 2A uses only fictional fixtures. Real schema inspection,
coverage decisions, data-quality measurement, and selection-bias investigation
belong to Phase 2B; ranking and uncertainty work follows later.

**Rationale:** Synthetic records verify software behavior but provide no evidence
about Yelp or business-rating reliability. Keeping cleaning separate from the
research analysis makes assumptions easier to audit and prevents fabricated
findings.

