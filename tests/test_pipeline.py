"""End-to-end tests for safe synthetic ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

import rating_reliability.ingest as ingest_module
from rating_reliability.ingest import IngestionError, run_ingestion

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_BUSINESSES = PROJECT_ROOT / "data/sample/synthetic_businesses.jsonl"
SAMPLE_REVIEWS = PROJECT_ROOT / "data/sample/synthetic_reviews.jsonl"
EMPTY_BUSINESSES = PROJECT_ROOT / "data/sample/empty_businesses.jsonl"
EMPTY_REVIEWS = PROJECT_ROOT / "data/sample/empty_reviews.jsonl"


def _outputs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "analytics.duckdb", tmp_path / "quality.json"


def _run_sample(tmp_path: Path, *, batch_size: int = 2, overwrite: bool = False):
    database, quality = _outputs(tmp_path)
    report = run_ingestion(
        businesses=SAMPLE_BUSINESSES,
        reviews=SAMPLE_REVIEWS,
        database=database,
        quality_report=quality,
        batch_size=batch_size,
        overwrite=overwrite,
    )
    return report, database, quality


def test_synthetic_pipeline_counts_reconcile_and_report_is_valid_json(
    tmp_path: Path,
) -> None:
    report, _, quality_path = _run_sample(tmp_path, batch_size=2)
    saved_report = json.loads(quality_path.read_text(encoding="utf-8"))

    assert report.businesses.total_lines == 12
    assert report.businesses.blank_lines == 1
    assert report.businesses.accepted_records == 5
    assert report.businesses.rejected_records == 6
    assert report.businesses.identical_duplicates == 1
    assert report.businesses.conflicting_duplicates == 1
    assert report.businesses.missing_optional_field_warnings == 8
    assert report.reviews.total_lines == 20
    assert report.reviews.blank_lines == 1
    assert report.reviews.accepted_records == 7
    assert report.reviews.rejected_records == 12
    assert report.reviews.invalid_star_ratings == 3
    assert report.reviews.invalid_dates == 1
    assert report.reviews.orphaned_reviews == 1
    assert saved_report["report_schema_version"] == 1
    assert saved_report["reviews"]["accepted_records"] == 7
    assert saved_report["counting_rules"]["issue_counters_may_overlap"] is True


def test_synthetic_database_has_expected_schema_and_integrity(tmp_path: Path) -> None:
    _, database, _ = _run_sample(tmp_path, batch_size=1)

    with duckdb.connect(str(database), read_only=True) as connection:
        tables = connection.execute("SHOW TABLES").fetchall()
        business_count = connection.execute(
            "SELECT count(*) FROM businesses"
        ).fetchone()[0]
        review_count = connection.execute("SELECT count(*) FROM reviews").fetchone()[0]
        duplicate_reviews = connection.execute(
            "SELECT count(*) - count(DISTINCT review_id) FROM reviews"
        ).fetchone()[0]
        orphans = connection.execute(
            """
            SELECT count(*) FROM reviews AS r
            LEFT JOIN businesses AS b USING (business_id)
            WHERE b.business_id IS NULL
            """
        ).fetchone()[0]
        rating_range = connection.execute(
            "SELECT min(stars), max(stars) FROM reviews"
        ).fetchone()
        source_and_computed = connection.execute(
            """
            SELECT b.source_stars, avg(r.stars)
            FROM businesses AS b JOIN reviews AS r USING (business_id)
            WHERE b.business_id = ?
            GROUP BY b.source_stars
            """,
            ["biz-001"],
        ).fetchone()
        business_columns = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('businesses')").fetchall()
        }

    assert tables == [("businesses",), ("reviews",)]
    assert business_count == 5
    assert review_count == 7
    assert duplicate_reviews == 0
    assert orphans == 0
    assert rating_range == (1, 5)
    assert source_and_computed == (4.5, 3.0)
    assert business_columns["source_stars"] == "DOUBLE"
    assert business_columns["source_review_count"] == "BIGINT"


def test_empty_inputs_create_empty_valid_tables_and_zero_rates(tmp_path: Path) -> None:
    database, quality = _outputs(tmp_path)

    report = run_ingestion(
        businesses=EMPTY_BUSINESSES,
        reviews=EMPTY_REVIEWS,
        database=database,
        quality_report=quality,
        batch_size=3,
    )

    assert report.businesses.accepted_records == 0
    assert report.businesses.acceptance_rate == 0.0
    assert report.reviews.rejected_records == 0
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM businesses").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM reviews").fetchone()[0] == 0


def test_pipeline_refuses_accidental_overwrite(tmp_path: Path) -> None:
    _run_sample(tmp_path)

    with pytest.raises(IngestionError, match="--overwrite"):
        _run_sample(tmp_path)


def test_explicit_overwrite_replaces_without_accumulating_records(
    tmp_path: Path,
) -> None:
    _run_sample(tmp_path)
    report, database, _ = _run_sample(tmp_path, batch_size=3, overwrite=True)

    assert report.reviews.accepted_records == 7
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM reviews").fetchone()[0] == 7


def test_ingestion_failure_preserves_previous_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, database, quality = _run_sample(tmp_path)
    original_database = database.read_bytes()
    original_quality = quality.read_bytes()

    def fail_finalization(connection: duckdb.DuckDBPyConnection) -> None:
        raise OSError("synthetic finalization failure")

    monkeypatch.setattr(ingest_module, "finalize_database", fail_finalization)

    with pytest.raises(IngestionError, match="synthetic finalization failure"):
        _run_sample(tmp_path, overwrite=True)

    assert database.read_bytes() == original_database
    assert quality.read_bytes() == original_quality


def test_missing_input_is_reported_before_output_creation(tmp_path: Path) -> None:
    database, quality = _outputs(tmp_path)

    with pytest.raises(IngestionError, match="does not exist"):
        run_ingestion(
            businesses=tmp_path / "missing.jsonl",
            reviews=SAMPLE_REVIEWS,
            database=database,
            quality_report=quality,
        )

    assert not database.exists()
    assert not quality.exists()


@pytest.mark.parametrize("batch_size", [0, -1, True])
def test_pipeline_rejects_invalid_batch_size(tmp_path: Path, batch_size: int) -> None:
    database, quality = _outputs(tmp_path)

    with pytest.raises(IngestionError, match="positive integer"):
        run_ingestion(
            businesses=SAMPLE_BUSINESSES,
            reviews=SAMPLE_REVIEWS,
            database=database,
            quality_report=quality,
            batch_size=batch_size,
        )
