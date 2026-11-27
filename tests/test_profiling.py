"""Tests for aggregate DuckDB profiling, populations, and local EDA outputs."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from rating_reliability.ingest import run_ingestion
from rating_reliability.profiling import (
    FIGURE_NAMES,
    REPORT_NAMES,
    ProfilingError,
    build_dataset_profile,
    build_population_summary,
    main,
    run_profiling,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUSINESSES = PROJECT_ROOT / "data/sample/synthetic_businesses.jsonl"
REVIEWS = PROJECT_ROOT / "data/sample/synthetic_reviews.jsonl"
EMPTY_BUSINESSES = PROJECT_ROOT / "data/sample/empty_businesses.jsonl"
EMPTY_REVIEWS = PROJECT_ROOT / "data/sample/empty_reviews.jsonl"
SQL_DIRECTORY = PROJECT_ROOT / "sql/analysis"


def _ingest(tmp_path: Path, *, empty: bool = False) -> tuple[Path, Path]:
    database = tmp_path / "validated.duckdb"
    quality = tmp_path / "ingestion-quality.json"
    run_ingestion(
        businesses=EMPTY_BUSINESSES if empty else BUSINESSES,
        reviews=EMPTY_REVIEWS if empty else REVIEWS,
        database=database,
        quality_report=quality,
        batch_size=2,
    )
    return database, quality


def _profile(database: Path) -> dict[str, object]:
    with duckdb.connect(str(database), read_only=True) as connection:
        return build_dataset_profile(
            connection,
            dataset_source="synthetic fixtures",
            dataset_version="1",
            permission_status="synthetic",
            processing_date="2026-11-17",
            database_path=database,
            input_files=(BUSINESSES, REVIEWS),
        )


def test_profile_calculates_independently_expected_descriptive_statistics(
    tmp_path: Path,
) -> None:
    database, _ = _ingest(tmp_path)

    profile = _profile(database)
    business = profile["business_coverage"]
    review = profile["review_coverage"]

    assert business["accepted_businesses"] == 5
    assert business["missing_city"] == 1
    assert business["missing_state"] == 2
    assert business["missing_categories"] == 1
    assert business["missing_source_stars"] == 2
    assert review["accepted_reviews"] == 7
    assert review["mean_rating"] == pytest.approx(23 / 7)
    assert review["median_rating"] == 3.0
    assert review["minimum_rating"] == 1
    assert review["maximum_rating"] == 5
    assert review["minimum_date"] == "2024-01-01"
    assert review["maximum_date"] == "2024-06-07"
    assert review["reviews_per_business"]["mean"] == pytest.approx(1.4)
    assert review["reviews_per_business"]["maximum"] == 3
    assert review["reviews_per_business"]["zero_review_businesses"] == 0
    assert [row["review_count"] for row in review["rating_distribution"]] == [
        1,
        1,
        2,
        1,
        2,
    ]


def test_profile_reports_time_volume_and_source_comparisons(tmp_path: Path) -> None:
    database, _ = _ingest(tmp_path)

    profile = _profile(database)

    assert profile["temporal_coverage"]["reviews_by_year"] == [
        {"year": 2024, "review_count": 7}
    ]
    assert [
        row["review_count"] for row in profile["temporal_coverage"]["reviews_by_month"]
    ] == [
        2,
        1,
        1,
        1,
        1,
        1,
    ]
    assert profile["review_volume_groups"] == [
        {
            "review_volume_group": "1-4",
            "business_count": 5,
            "accepted_review_count": 7,
            "mean_business_average": pytest.approx(3.4),
            "median_business_average": pytest.approx(3.0),
            "standard_deviation_business_average": pytest.approx(1.1401754251),
        }
    ]
    rating_comparison = profile["relational_quality"]["source_rating_comparison"]
    assert rating_comparison["comparable_businesses"] == 3
    assert rating_comparison["mean_absolute_difference"] == pytest.approx(11 / 6)
    assert profile["relational_quality"]["duplicate_review_ids"] == 0
    assert profile["relational_quality"]["orphaned_reviews_in_final_table"] == 0


def test_population_design_retains_low_volume_and_separates_stages(
    tmp_path: Path,
) -> None:
    database, _ = _ingest(tmp_path)

    population = build_population_summary(_profile(database))

    assert population["validated_ingestion_population"]["businesses"] == 5
    assert population["descriptive_analysis_population"]["businesses"] == 5
    assert population["statistical_comparison_population"]["businesses"] == 5
    assert population["statistical_comparison_population"]["minimum_reviews"] == 1
    assert (
        population["future_historical_evaluation_population"]["status"]
        == "not yet selected"
    )


def test_all_documented_sql_queries_execute_and_return_expected_results(
    tmp_path: Path,
) -> None:
    database, _ = _ingest(tmp_path)

    with duckdb.connect(str(database), read_only=True) as connection:
        business_rows = connection.execute(
            (SQL_DIRECTORY / "business_review_metrics.sql").read_text(encoding="utf-8")
        ).fetchall()
        rating_rows = connection.execute(
            (SQL_DIRECTORY / "rating_distribution.sql").read_text(encoding="utf-8")
        ).fetchall()
        monthly_rows = connection.execute(
            (SQL_DIRECTORY / "reviews_over_time.sql").read_text(encoding="utf-8")
        ).fetchall()
        source_rows = connection.execute(
            (SQL_DIRECTORY / "source_aggregate_comparison.sql").read_text(
                encoding="utf-8"
            )
        ).fetchall()
        category_rows = connection.execute(
            (SQL_DIRECTORY / "category_summary.sql").read_text(encoding="utf-8")
        ).fetchall()

    biz_one = next(row for row in business_rows if row[0] == "biz-001")
    assert biz_one[7] == 3
    assert biz_one[8] == pytest.approx(3.0)
    assert sum(row[1] for row in rating_rows) == 7
    assert sum(row[1] for row in monthly_rows) == 7
    assert len(source_rows) == 5
    assert {row[0] for row in category_rows} == {
        "Bakeries",
        "Bookstores",
        "Coffee & Tea",
        "Florists",
        "Sewing & Alterations",
    }


def test_local_output_generation_is_private_and_reproducible(tmp_path: Path) -> None:
    database, quality = _ingest(tmp_path)
    quality_payload = json.loads(quality.read_text(encoding="utf-8"))
    quality_payload["private_text"] = "SECRET_REVIEW_TEXT"
    quality.write_text(json.dumps(quality_payload), encoding="utf-8")

    first_reports = tmp_path / "reports-one"
    first_figures = tmp_path / "figures-one"
    second_reports = tmp_path / "reports-two"
    second_figures = tmp_path / "figures-two"
    common = {
        "database": database,
        "dataset_source": "synthetic fixtures",
        "dataset_version": "1",
        "permission_status": "synthetic",
        "processing_date": "2026-11-17",
        "input_files": (BUSINESSES, REVIEWS),
        "ingestion_quality_report": quality,
    }

    run_profiling(
        output_directory=first_reports,
        figures_directory=first_figures,
        **common,
    )
    run_profiling(
        output_directory=second_reports,
        figures_directory=second_figures,
        **common,
    )

    for report_name in REPORT_NAMES:
        assert (first_reports / report_name).read_bytes() == (
            second_reports / report_name
        ).read_bytes()
    for figure_name in FIGURE_NAMES:
        figure = first_figures / figure_name
        assert figure.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert figure.stat().st_size > 1_000
    combined_reports = "".join(
        path.read_text(encoding="utf-8") for path in first_reports.glob("*.json")
    )
    assert "SECRET_REVIEW_TEXT" not in combined_reports


def test_empty_tables_produce_zero_profiles_and_placeholder_figures(
    tmp_path: Path,
) -> None:
    database, quality = _ingest(tmp_path, empty=True)
    reports = tmp_path / "empty-reports"
    figures = tmp_path / "empty-figures"

    profile = run_profiling(
        database=database,
        output_directory=reports,
        figures_directory=figures,
        dataset_source="empty synthetic fixtures",
        dataset_version="1",
        permission_status="synthetic",
        processing_date="2026-11-17",
        ingestion_quality_report=quality,
    )

    assert profile["business_coverage"]["accepted_businesses"] == 0
    assert profile["review_coverage"]["accepted_reviews"] == 0
    assert profile["review_coverage"]["rating_distribution"] == []
    assert all((figures / name).exists() for name in FIGURE_NAMES)


def test_profiling_refuses_overwrite_and_unavailable_database(tmp_path: Path) -> None:
    database, quality = _ingest(tmp_path)
    reports = tmp_path / "reports"
    figures = tmp_path / "figures"
    arguments = {
        "database": database,
        "output_directory": reports,
        "figures_directory": figures,
        "dataset_source": "synthetic fixtures",
        "dataset_version": "1",
        "permission_status": "synthetic",
        "processing_date": "2026-11-17",
        "ingestion_quality_report": quality,
    }
    run_profiling(**arguments)

    with pytest.raises(ProfilingError, match="--overwrite"):
        run_profiling(**arguments)
    with pytest.raises(ProfilingError, match="does not exist"):
        run_profiling(**{**arguments, "database": tmp_path / "missing.duckdb"})


def test_profiling_cli_reports_success_and_missing_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, quality = _ingest(tmp_path)
    base = [
        "--database",
        str(database),
        "--output-dir",
        str(tmp_path / "cli-reports"),
        "--figures-dir",
        str(tmp_path / "cli-figures"),
        "--dataset-source",
        "synthetic fixtures",
        "--dataset-version",
        "1",
        "--permission-status",
        "synthetic",
        "--processing-date",
        "2026-11-17",
        "--ingestion-quality-report",
        str(quality),
    ]

    assert main(base) == 0
    output = capsys.readouterr()
    assert "accepted businesses profiled: 5" in output.out

    missing_arguments = base.copy()
    missing_arguments[1] = str(tmp_path / "missing.duckdb")
    assert main(missing_arguments) == 1
    output = capsys.readouterr()
    assert "does not exist" in output.err
    assert "Traceback" not in output.err
