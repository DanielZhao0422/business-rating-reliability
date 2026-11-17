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
configuration. Phase 1 has no runtime third-party dependency.

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

## ADR-006: Prefer DuckDB before PostgreSQL

**Decision:** Later local analytical SQL will begin with DuckDB. PostgreSQL will
be considered only if collaboration, services, or scale justify it.

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

