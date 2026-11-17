"""Tests for DuckDB schema constraints and database-derived outcomes."""

from datetime import date
from pathlib import Path

import duckdb
import pytest

from rating_reliability.storage import (
    connect_staging_database,
    finalize_database,
    insert_business_batch,
    insert_review_batch,
)
from rating_reliability.validation import BusinessRecord, ReviewRecord


def _business(business_id: str, name: str = "Fictional Shop") -> BusinessRecord:
    return BusinessRecord(business_id, name, None, None, None, 4.0, 1)


def _review(review_id: str, business_id: str, stars: int = 4) -> ReviewRecord:
    return ReviewRecord(review_id, business_id, stars, date(2024, 1, 1))


def test_storage_deduplicates_across_batches_and_excludes_orphans(
    tmp_path: Path,
) -> None:
    database = tmp_path / "staging.duckdb"
    connection = connect_staging_database(database)
    try:
        insert_business_batch(connection, [(1, _business("biz-1"))])
        insert_business_batch(
            connection,
            [
                (2, _business("biz-1")),
                (3, _business("biz-1", name="Conflicting Shop")),
            ],
        )
        insert_review_batch(connection, [(1, _review("rev-1", "biz-1", 1))])
        insert_review_batch(
            connection,
            [
                (2, _review("rev-1", "biz-1", 1)),
                (3, _review("rev-1", "biz-1", 5)),
                (4, _review("rev-orphan", "missing", 3)),
            ],
        )

        stats = finalize_database(connection)

        assert stats.accepted_businesses == 1
        assert stats.identical_business_duplicates == 1
        assert stats.conflicting_business_duplicates == 1
        assert stats.accepted_reviews == 1
        assert stats.identical_review_duplicates == 1
        assert stats.conflicting_review_duplicates == 1
        assert stats.orphaned_reviews == 1
        assert connection.execute("SHOW TABLES").fetchall() == [
            ("businesses",),
            ("reviews",),
        ]
    finally:
        connection.close()


def test_final_tables_enforce_unique_identifiers_and_relationships(
    tmp_path: Path,
) -> None:
    connection = connect_staging_database(tmp_path / "constraints.duckdb")
    try:
        insert_business_batch(connection, [(1, _business("biz-1"))])
        finalize_database(connection)

        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO businesses VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["biz-1", "Duplicate", None, None, None, 4.0, 1],
            )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?)",
                ["rev-1", "missing", 4, date(2024, 1, 1)],
            )
    finally:
        connection.close()


def test_parameterized_query_returns_expected_record(tmp_path: Path) -> None:
    connection = connect_staging_database(tmp_path / "query.duckdb")
    try:
        insert_business_batch(connection, [(1, _business("biz-1"))])
        finalize_database(connection)

        row = connection.execute(
            "SELECT name FROM businesses WHERE business_id = ?", ["biz-1"]
        ).fetchone()

        assert row == ("Fictional Shop",)
    finally:
        connection.close()


def test_closed_database_reports_database_error(tmp_path: Path) -> None:
    connection = duckdb.connect(str(tmp_path / "closed.duckdb"))
    connection.close()

    with pytest.raises(duckdb.ConnectionException):
        connection.execute("SELECT 1")
