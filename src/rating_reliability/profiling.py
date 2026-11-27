"""Reproducible DuckDB profiling, population summaries, and local EDA figures."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "rating-reliability-matplotlib")
)
import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from rating_reliability.artifacts import ArtifactError, sha256_file, write_json_artifact
from rating_reliability.config import PROJECT_ROOT


class ProfilingError(Exception):
    """An expected profiling failure suitable for concise CLI display."""


REPORT_NAMES = (
    "dataset_profile.json",
    "data_quality_summary.json",
    "population_summary.json",
)
FIGURE_NAMES = (
    "rating_distribution.png",
    "review_count_distribution.png",
    "reviews_over_time.png",
    "review_volume_vs_rating.png",
)


def _sql(name: str) -> str:
    path = PROJECT_ROOT / "sql" / "analysis" / name
    try:
        return path.read_text(encoding="utf-8").strip().removesuffix(";")
    except OSError as error:
        raise ProfilingError(f"cannot read analysis SQL {path}: {error}") from error


def _scalar(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    row = connection.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def _json_number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return float(value)


def _summary_row(
    row: tuple[object, ...], columns: Sequence[str]
) -> dict[str, int | float | None]:
    return {
        column: _json_number(value) for column, value in zip(columns, row, strict=True)
    }


def _validate_database(connection: duckdb.DuckDBPyConnection) -> None:
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    missing = {"businesses", "reviews"} - tables
    if missing:
        raise ProfilingError(
            f"database is missing required tables: {', '.join(sorted(missing))}"
        )


def _input_metadata(paths: Sequence[Path]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in paths:
        if not path.is_file():
            raise ProfilingError(f"metadata input file does not exist: {path}")
        result.append(
            {
                "file_name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return sorted(result, key=lambda item: str(item["file_name"]))


def _business_coverage(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT
            count(*) AS accepted_businesses,
            count(*) FILTER (WHERE name IS NULL) AS missing_name,
            count(*) FILTER (WHERE city IS NULL) AS missing_city,
            count(*) FILTER (WHERE state IS NULL) AS missing_state,
            count(*) FILTER (WHERE categories IS NULL) AS missing_categories,
            count(*) FILTER (WHERE source_stars IS NULL) AS missing_source_stars,
            count(*) FILTER (WHERE source_review_count IS NULL)
                AS missing_source_review_count,
            count(DISTINCT city) FILTER (WHERE city IS NOT NULL) AS distinct_cities,
            count(DISTINCT state) FILTER (WHERE state IS NOT NULL) AS distinct_states
        FROM businesses
        """
    ).fetchone()
    assert row is not None
    columns = (
        "accepted_businesses",
        "missing_name",
        "missing_city",
        "missing_state",
        "missing_categories",
        "missing_source_stars",
        "missing_source_review_count",
        "distinct_cities",
        "distinct_states",
    )
    coverage: dict[str, object] = _summary_row(row, columns)

    source_counts = connection.execute(
        """
        SELECT
            count(source_review_count),
            avg(source_review_count),
            median(source_review_count),
            stddev_samp(source_review_count),
            quantile_cont(source_review_count, 0.25),
            quantile_cont(source_review_count, 0.75),
            min(source_review_count),
            max(source_review_count)
        FROM businesses
        """
    ).fetchone()
    assert source_counts is not None
    coverage["source_review_count_distribution"] = _summary_row(
        source_counts,
        (
            "count",
            "mean",
            "median",
            "standard_deviation",
            "q25",
            "q75",
            "minimum",
            "maximum",
        ),
    )
    coverage["top_states"] = [
        {"state": state, "business_count": int(count)}
        for state, count in connection.execute(
            """
            SELECT state, count(*)
            FROM businesses
            WHERE state IS NOT NULL
            GROUP BY state
            ORDER BY count(*) DESC, state
            LIMIT 20
            """
        ).fetchall()
    ]
    return coverage


def _review_coverage(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT
            count(*), avg(stars), median(stars), stddev_samp(stars),
            quantile_cont(stars, 0.25), quantile_cont(stars, 0.75),
            min(stars), max(stars), min(date), max(date)
        FROM reviews
        """
    ).fetchone()
    assert row is not None
    coverage: dict[str, object] = _summary_row(
        row[:8],
        (
            "accepted_reviews",
            "mean_rating",
            "median_rating",
            "standard_deviation",
            "q25",
            "q75",
            "minimum_rating",
            "maximum_rating",
        ),
    )
    coverage["minimum_date"] = row[8].isoformat() if row[8] else None
    coverage["maximum_date"] = row[9].isoformat() if row[9] else None

    business_sql = _sql("business_review_metrics.sql")
    count_row = connection.execute(
        f"""
        WITH business_metrics AS ({business_sql})
        SELECT
            count(*), avg(accepted_review_count), median(accepted_review_count),
            stddev_samp(accepted_review_count),
            quantile_cont(accepted_review_count, 0.25),
            quantile_cont(accepted_review_count, 0.75),
            min(accepted_review_count), max(accepted_review_count),
            count(*) FILTER (WHERE accepted_review_count = 0),
            count(*) FILTER (WHERE accepted_review_count > 0)
        FROM business_metrics
        """
    ).fetchone()
    assert count_row is not None
    coverage["reviews_per_business"] = _summary_row(
        count_row,
        (
            "business_count",
            "mean",
            "median",
            "standard_deviation",
            "q25",
            "q75",
            "minimum",
            "maximum",
            "zero_review_businesses",
            "businesses_with_reviews",
        ),
    )

    rating_rows = connection.execute(_sql("rating_distribution.sql")).fetchall()
    coverage["rating_distribution"] = [
        {
            "stars": int(stars),
            "review_count": int(count),
            "proportion": float(proportion),
        }
        for stars, count, proportion in rating_rows
    ]
    return coverage


def _temporal_coverage(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    monthly_rows = connection.execute(_sql("reviews_over_time.sql")).fetchall()
    annual_rows = connection.execute(
        """
        SELECT year(date) AS review_year, count(*) AS review_count
        FROM reviews
        GROUP BY review_year
        ORDER BY review_year
        """
    ).fetchall()
    return {
        "reviews_by_year": [
            {"year": int(year), "review_count": int(count)}
            for year, count in annual_rows
        ],
        "reviews_by_month": [
            {
                "month": month.isoformat(),
                "review_count": int(count),
                "observed_mean_rating": float(mean),
            }
            for month, count, mean in monthly_rows
        ],
    }


def _category_coverage(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    rows = connection.execute(_sql("category_summary.sql")).fetchall()
    return {
        "distinct_categories": len(rows),
        "top_categories": [
            {
                "category": category,
                "business_count": int(business_count),
                "accepted_review_count": int(review_count),
                "review_weighted_mean_rating": (
                    float(weighted_mean) if weighted_mean is not None else None
                ),
            }
            for category, business_count, review_count, weighted_mean in rows[:20]
        ],
    }


def _relational_quality(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    source_sql = _sql("source_aggregate_comparison.sql")
    comparison = connection.execute(
        f"""
        WITH comparisons AS ({source_sql})
        SELECT
            count(*) FILTER (
                WHERE source_stars IS NOT NULL
                  AND reconstructed_mean_rating IS NOT NULL
            ),
            avg(abs(rating_difference)) FILTER (
                WHERE source_stars IS NOT NULL
                  AND reconstructed_mean_rating IS NOT NULL
            ),
            median(abs(rating_difference)) FILTER (
                WHERE source_stars IS NOT NULL
                  AND reconstructed_mean_rating IS NOT NULL
            ),
            count(*) FILTER (
                WHERE source_review_count IS NOT NULL
                  AND reconstructed_review_count IS NOT NULL
            ),
            avg(abs(count_difference)) FILTER (
                WHERE source_review_count IS NOT NULL
                  AND reconstructed_review_count IS NOT NULL
            )
        FROM comparisons
        """
    ).fetchone()
    assert comparison is not None
    return {
        "duplicate_business_ids": _scalar(
            connection,
            "SELECT count(*) - count(DISTINCT business_id) FROM businesses",
        ),
        "duplicate_review_ids": _scalar(
            connection, "SELECT count(*) - count(DISTINCT review_id) FROM reviews"
        ),
        "orphaned_reviews_in_final_table": _scalar(
            connection,
            """
            SELECT count(*) FROM reviews AS r
            LEFT JOIN businesses AS b USING (business_id)
            WHERE b.business_id IS NULL
            """,
        ),
        "source_rating_comparison": {
            "comparable_businesses": int(comparison[0]),
            "mean_absolute_difference": _json_number(comparison[1]),
            "median_absolute_difference": _json_number(comparison[2]),
        },
        "source_review_count_comparison": {
            "comparable_businesses": int(comparison[3]),
            "mean_absolute_difference": _json_number(comparison[4]),
        },
        "interpretation": (
            "Differences are descriptive, not automatic data-quality errors; "
            "source populations, cutoffs, rounding, sampling, filtering, or "
            "unknown source behavior may differ."
        ),
    }


def _volume_groups(connection: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
    business_sql = _sql("business_review_metrics.sql")
    rows = connection.execute(
        f"""
        WITH business_metrics AS ({business_sql}),
        labeled AS (
            SELECT
                CASE
                    WHEN accepted_review_count = 0 THEN '0'
                    WHEN accepted_review_count BETWEEN 1 AND 4 THEN '1-4'
                    WHEN accepted_review_count BETWEEN 5 AND 19 THEN '5-19'
                    WHEN accepted_review_count BETWEEN 20 AND 99 THEN '20-99'
                    ELSE '100+'
                END AS review_volume_group,
                accepted_review_count,
                observed_mean_rating
            FROM business_metrics
        )
        SELECT
            review_volume_group,
            count(*),
            sum(accepted_review_count),
            avg(observed_mean_rating),
            median(observed_mean_rating),
            stddev_samp(observed_mean_rating)
        FROM labeled
        GROUP BY review_volume_group
        ORDER BY min(accepted_review_count)
        """
    ).fetchall()
    return [
        {
            "review_volume_group": group,
            "business_count": int(business_count),
            "accepted_review_count": int(review_count),
            "mean_business_average": _json_number(mean),
            "median_business_average": _json_number(median),
            "standard_deviation_business_average": _json_number(stddev),
        }
        for group, business_count, review_count, mean, median, stddev in rows
    ]


def build_dataset_profile(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_source: str,
    dataset_version: str,
    permission_status: str,
    processing_date: str,
    database_path: Path,
    input_files: Sequence[Path] = (),
) -> dict[str, object]:
    """Build aggregate descriptive results without review text or row-level data."""

    _validate_database(connection)
    try:
        date.fromisoformat(processing_date)
    except ValueError as error:
        raise ProfilingError("processing date must use YYYY-MM-DD") from error

    return {
        "report_schema_version": 1,
        "metadata": {
            "dataset_source": dataset_source,
            "dataset_version": dataset_version,
            "permission_status": permission_status,
            "processing_date": processing_date,
            "database_file_name": database_path.name,
            "database_size_bytes": database_path.stat().st_size,
            "database_sha256": sha256_file(database_path),
            "input_files": _input_metadata(input_files),
            "validation_rules": "rating_reliability Phase 2A schema version 1",
        },
        "business_coverage": _business_coverage(connection),
        "review_coverage": _review_coverage(connection),
        "temporal_coverage": _temporal_coverage(connection),
        "category_coverage": _category_coverage(connection),
        "review_volume_groups": _volume_groups(connection),
        "relational_quality": _relational_quality(connection),
        "interpretation_limits": [
            "Descriptive associations do not establish causation.",
            "Accepted reviews need not represent all customers or experiences.",
            "Source aggregate differences are not automatically errors.",
            "No ranking or advanced statistical model is produced in Phase 2B.",
        ],
        "privacy": {"review_text_included": False, "row_level_records_included": False},
    }


def build_population_summary(profile: Mapping[str, Any]) -> dict[str, object]:
    """Define explicit analysis stages without imposing a high-volume cutoff."""

    business = profile["business_coverage"]
    review = profile["review_coverage"]
    review_counts = review["reviews_per_business"]
    accepted_businesses = int(business["accepted_businesses"])
    accepted_reviews = int(review["accepted_reviews"])
    businesses_with_reviews = int(review_counts["businesses_with_reviews"])
    zero_review_businesses = int(review_counts["zero_review_businesses"])
    return {
        "report_schema_version": 1,
        "source_population": {
            "status": "defined by local source files and ingestion quality report",
            "counts": "see data_quality_summary.json when supplied",
        },
        "validated_ingestion_population": {
            "businesses": accepted_businesses,
            "reviews": accepted_reviews,
            "definition": "Phase 2A validated, deduplicated, relationally valid tables",
        },
        "descriptive_analysis_population": {
            "businesses": accepted_businesses,
            "reviews": accepted_reviews,
            "zero_review_businesses_retained": zero_review_businesses,
            "definition": (
                "all accepted businesses and reviews; no rating or volume filter"
            ),
        },
        "statistical_comparison_population": {
            "businesses": businesses_with_reviews,
            "reviews": accepted_reviews,
            "minimum_reviews": 1,
            "definition": (
                "businesses with at least one accepted rating; low-volume businesses "
                "remain central and volume groups are compared rather than discarded"
            ),
        },
        "future_historical_evaluation_population": {
            "status": "not yet selected",
            "rule": (
                "use a documented historical cutoff and determine eligibility only "
                "from information available before that cutoff"
            ),
        },
        "bias_risks": [
            "review participation bias",
            "survivorship bias",
            "geographic imbalance",
            "category imbalance",
            "time-related coverage and platform-change bias",
            "selection introduced by missing or invalid required fields",
        ],
        "missing_review_assumption": (
            "no assumption that missing reviews are positive or negative"
        ),
    }


def _quality_summary(path: Path | None) -> dict[str, object]:
    if path is None:
        return {
            "report_schema_version": 1,
            "status": "not_provided",
            "privacy": {"review_text_included": False},
        }
    if not path.is_file():
        raise ProfilingError(f"ingestion quality report does not exist: {path}")
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfilingError(
            f"cannot read ingestion quality report: {error}"
        ) from error

    allowed_summary_fields = {
        "total_lines",
        "blank_lines",
        "successfully_parsed_records",
        "malformed_json_records",
        "non_object_json_records",
        "schema_invalid_records",
        "accepted_records",
        "rejected_records",
        "duplicate_identifiers",
        "identical_duplicates",
        "conflicting_duplicates",
        "missing_required_identifiers",
        "invalid_star_ratings",
        "invalid_dates",
        "orphaned_reviews",
        "missing_optional_field_warnings",
        "processed_records",
        "acceptance_rate",
        "rejection_rate",
        "issue_counts",
    }

    def sanitized(dataset: str) -> dict[str, object]:
        section = source.get(dataset, {})
        if not isinstance(section, dict):
            raise ProfilingError(f"invalid quality report section: {dataset}")
        return {
            key: value
            for key, value in section.items()
            if key in allowed_summary_fields
        }

    return {
        "report_schema_version": 1,
        "status": "provided",
        "source_file_name": path.name,
        "businesses": sanitized("businesses"),
        "reviews": sanitized("reviews"),
        "counting_rules": source.get("counting_rules", {}),
        "privacy": {"review_text_included": False, "source_records_included": False},
    }


def _save_figure(figure: Figure, destination: Path, *, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        raise ArtifactError(
            f"output already exists; use --overwrite to replace: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.stem}.staging-{uuid4().hex}{destination.suffix}"
    )
    try:
        figure.savefig(
            temporary,
            dpi=150,
            bbox_inches="tight",
            metadata={"Software": "rating_reliability"},
        )
        if destination.exists() and not overwrite:
            raise ArtifactError(
                f"output appeared during generation; refusing to replace: {destination}"
            )
        os.replace(temporary, destination)
    except ArtifactError:
        raise
    except OSError as error:
        raise ArtifactError(f"cannot write figure: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _empty_figure(title: str) -> Figure:
    figure = Figure(figsize=(7, 4.5))
    axis = figure.subplots()
    axis.set_title(title)
    axis.text(0.5, 0.5, "No accepted data available", ha="center", va="center")
    axis.set_axis_off()
    return figure


def generate_figures(
    connection: duckdb.DuckDBPyConnection,
    destination: Path,
    *,
    overwrite: bool = False,
) -> None:
    """Generate four aggregate EDA figures without row labels or review text."""

    destination.mkdir(parents=True, exist_ok=True)
    rating_rows = connection.execute(_sql("rating_distribution.sql")).fetchall()
    if rating_rows:
        figure = Figure(figsize=(7, 4.5))
        axis = figure.subplots()
        axis.bar(
            [int(row[0]) for row in rating_rows], [int(row[1]) for row in rating_rows]
        )
        axis.set(
            title="Accepted individual rating distribution",
            xlabel="Stars",
            ylabel="Reviews",
        )
        axis.set_xticks(range(1, 6))
    else:
        figure = _empty_figure("Accepted individual rating distribution")
    _save_figure(figure, destination / FIGURE_NAMES[0], overwrite=overwrite)

    business_sql = _sql("business_review_metrics.sql")
    metrics = connection.execute(
        f"""
        WITH business_metrics AS ({business_sql})
        SELECT accepted_review_count, observed_mean_rating
        FROM business_metrics
        """
    ).fetchall()
    counts = [int(row[0]) for row in metrics]
    if counts:
        figure = Figure(figsize=(7, 4.5))
        axis = figure.subplots()
        axis.hist(counts, bins=min(30, max(5, len(set(counts)))))
        axis.set(
            title="Accepted review counts per business",
            xlabel="Accepted reviews",
            ylabel="Businesses",
        )
        positive = [count for count in counts if count > 0]
        if positive and max(positive) / min(positive) >= 100:
            axis.set_xscale("log")
            axis.set_xlabel("Accepted reviews (log scale)")
    else:
        figure = _empty_figure("Accepted review counts per business")
    _save_figure(figure, destination / FIGURE_NAMES[1], overwrite=overwrite)

    monthly = connection.execute(_sql("reviews_over_time.sql")).fetchall()
    if monthly:
        figure = Figure(figsize=(8, 4.5))
        axis = figure.subplots()
        axis.plot(
            [row[0] for row in monthly], [int(row[1]) for row in monthly], marker="o"
        )
        axis.set(title="Accepted reviews over time", xlabel="Month", ylabel="Reviews")
        figure.autofmt_xdate()
    else:
        figure = _empty_figure("Accepted reviews over time")
    _save_figure(figure, destination / FIGURE_NAMES[2], overwrite=overwrite)

    scatter = [
        (int(count), float(mean))
        for count, mean in metrics
        if count and mean is not None
    ]
    if scatter:
        figure = Figure(figsize=(7, 4.5))
        axis = figure.subplots()
        axis.scatter(
            [row[0] for row in scatter], [row[1] for row in scatter], alpha=0.6
        )
        axis.set(
            title="Review volume versus observed average rating",
            xlabel="Accepted reviews",
            ylabel="Observed average rating",
            ylim=(0.8, 5.2),
        )
        if max(row[0] for row in scatter) / min(row[0] for row in scatter) >= 100:
            axis.set_xscale("log")
            axis.set_xlabel("Accepted reviews (log scale)")
    else:
        figure = _empty_figure("Review volume versus observed average rating")
    _save_figure(figure, destination / FIGURE_NAMES[3], overwrite=overwrite)


def run_profiling(
    *,
    database: Path,
    output_directory: Path,
    figures_directory: Path,
    dataset_source: str,
    dataset_version: str,
    permission_status: str,
    processing_date: str,
    input_files: Sequence[Path] = (),
    ingestion_quality_report: Path | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Generate local aggregate reports and figures from validated DuckDB tables."""

    if not database.is_file():
        raise ProfilingError(f"database does not exist: {database}")
    destinations = [output_directory / name for name in REPORT_NAMES]
    destinations.extend(figures_directory / name for name in FIGURE_NAMES)
    existing = [path for path in destinations if path.exists()]
    if existing and not overwrite:
        raise ProfilingError(
            "outputs already exist; use --overwrite to replace: "
            + ", ".join(str(path) for path in existing)
        )

    try:
        connection = duckdb.connect(str(database), read_only=True)
    except duckdb.Error as error:
        raise ProfilingError(f"cannot open DuckDB database: {error}") from error
    try:
        profile = build_dataset_profile(
            connection,
            dataset_source=dataset_source,
            dataset_version=dataset_version,
            permission_status=permission_status,
            processing_date=processing_date,
            database_path=database,
            input_files=input_files,
        )
        population = build_population_summary(profile)
        quality = _quality_summary(ingestion_quality_report)
        generate_figures(connection, figures_directory, overwrite=overwrite)
    except duckdb.Error as error:
        raise ProfilingError(f"profiling query failed: {error}") from error
    finally:
        connection.close()

    write_json_artifact(profile, destinations[0], overwrite=overwrite)
    write_json_artifact(quality, destinations[1], overwrite=overwrite)
    write_json_artifact(population, destinations[2], overwrite=overwrite)
    return profile


def build_parser() -> argparse.ArgumentParser:
    """Build the local profiling CLI parser."""

    parser = argparse.ArgumentParser(
        description="Profile validated DuckDB tables and generate local EDA artifacts."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/local"))
    parser.add_argument(
        "--figures-dir", type=Path, default=Path("reports/figures/local")
    )
    parser.add_argument("--dataset-source", required=True)
    parser.add_argument("--dataset-version", default="unknown")
    parser.add_argument(
        "--permission-status",
        required=True,
        choices=("synthetic", "verified", "unverified", "blocked"),
    )
    parser.add_argument("--processing-date", default=date.today().isoformat())
    parser.add_argument("--input-file", action="append", type=Path, default=[])
    parser.add_argument("--ingestion-quality-report", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run local profiling and return a conventional process exit code."""

    args = build_parser().parse_args(argv)
    try:
        profile = run_profiling(
            database=args.database,
            output_directory=args.output_dir,
            figures_directory=args.figures_dir,
            dataset_source=args.dataset_source,
            dataset_version=args.dataset_version,
            permission_status=args.permission_status,
            processing_date=args.processing_date,
            input_files=args.input_file,
            ingestion_quality_report=args.ingestion_quality_report,
            overwrite=args.overwrite,
        )
    except (ProfilingError, ArtifactError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    business_count = profile["business_coverage"]["accepted_businesses"]
    review_count = profile["review_coverage"]["accepted_reviews"]
    print(f"accepted businesses profiled: {business_count}")
    print(f"accepted reviews profiled: {review_count}")
    print(f"reports: {args.output_dir}")
    print(f"figures: {args.figures_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
