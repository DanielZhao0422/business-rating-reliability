"""DuckDB schema, batched writes, and relational finalization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import duckdb

from rating_reliability.validation import BusinessRecord, ReviewRecord


@dataclass(frozen=True)
class StorageStats:
    """Database-derived counts used to finalize quality summaries."""

    accepted_businesses: int
    identical_business_duplicates: int
    conflicting_business_duplicates: int
    accepted_reviews: int
    identical_review_duplicates: int
    conflicting_review_duplicates: int
    orphaned_reviews: int


def staged_database_path(destination: Path) -> Path:
    """Return a unique DuckDB staging path beside the final destination."""

    return destination.with_name(f".{destination.stem}.staging-{uuid4().hex}.duckdb")


def connect_staging_database(path: Path) -> duckdb.DuckDBPyConnection:
    """Create a new staging database and its internal candidate tables."""

    connection = duckdb.connect(str(path))
    connection.execute(
        """
        CREATE TABLE business_candidates (
            source_line_number BIGINT NOT NULL,
            business_id VARCHAR NOT NULL,
            name VARCHAR,
            city VARCHAR,
            state VARCHAR,
            categories VARCHAR,
            source_stars DOUBLE,
            source_review_count BIGINT
        );

        CREATE TABLE review_candidates (
            source_line_number BIGINT NOT NULL,
            review_id VARCHAR NOT NULL,
            business_id VARCHAR NOT NULL,
            stars TINYINT NOT NULL,
            date DATE NOT NULL
        );
        """
    )
    return connection


def insert_business_batch(
    connection: duckdb.DuckDBPyConnection,
    records: Sequence[tuple[int, BusinessRecord]],
) -> None:
    """Insert one bounded batch of normalized business candidates."""

    if not records:
        return
    values = [
        (
            line_number,
            record.business_id,
            record.name,
            record.city,
            record.state,
            record.categories,
            record.source_stars,
            record.source_review_count,
        )
        for line_number, record in records
    ]
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.executemany(
            """
            INSERT INTO business_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def insert_review_batch(
    connection: duckdb.DuckDBPyConnection,
    records: Sequence[tuple[int, ReviewRecord]],
) -> None:
    """Insert one bounded batch of normalized review candidates."""

    if not records:
        return
    values = [
        (
            line_number,
            record.review_id,
            record.business_id,
            record.stars,
            record.date,
        )
        for line_number, record in records
    ]
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.executemany(
            """
            INSERT INTO review_candidates VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def _business_duplicate_counts(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[int, int]:
    row = connection.execute(
        """
        WITH ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY business_id ORDER BY source_line_number
                ) AS occurrence,
                first_value(name) OVER business_window AS first_name,
                first_value(city) OVER business_window AS first_city,
                first_value(state) OVER business_window AS first_state,
                first_value(categories) OVER business_window AS first_categories,
                first_value(source_stars) OVER business_window AS first_source_stars,
                first_value(source_review_count) OVER business_window
                    AS first_source_review_count
            FROM business_candidates
            WINDOW business_window AS (
                PARTITION BY business_id
                ORDER BY source_line_number
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )
        )
        SELECT
            coalesce(sum(CASE WHEN occurrence > 1 AND
                name IS NOT DISTINCT FROM first_name AND
                city IS NOT DISTINCT FROM first_city AND
                state IS NOT DISTINCT FROM first_state AND
                categories IS NOT DISTINCT FROM first_categories AND
                source_stars IS NOT DISTINCT FROM first_source_stars AND
                source_review_count IS NOT DISTINCT FROM first_source_review_count
                THEN 1 ELSE 0 END), 0) AS identical,
            coalesce(sum(CASE WHEN occurrence > 1 AND NOT (
                name IS NOT DISTINCT FROM first_name AND
                city IS NOT DISTINCT FROM first_city AND
                state IS NOT DISTINCT FROM first_state AND
                categories IS NOT DISTINCT FROM first_categories AND
                source_stars IS NOT DISTINCT FROM first_source_stars AND
                source_review_count IS NOT DISTINCT FROM first_source_review_count
                ) THEN 1 ELSE 0 END), 0) AS conflicting
        FROM ranked
        """
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def _review_duplicate_counts(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[int, int]:
    row = connection.execute(
        """
        WITH ranked AS (
            SELECT
                *,
                row_number() OVER (
                    PARTITION BY review_id ORDER BY source_line_number
                ) AS occurrence,
                first_value(business_id) OVER review_window AS first_business_id,
                first_value(stars) OVER review_window AS first_stars,
                first_value(date) OVER review_window AS first_date
            FROM review_candidates
            WINDOW review_window AS (
                PARTITION BY review_id
                ORDER BY source_line_number
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
            )
        )
        SELECT
            coalesce(sum(CASE WHEN occurrence > 1 AND
                business_id = first_business_id AND
                stars = first_stars AND date = first_date
                THEN 1 ELSE 0 END), 0) AS identical,
            coalesce(sum(CASE WHEN occurrence > 1 AND NOT (
                business_id = first_business_id AND
                stars = first_stars AND date = first_date
                ) THEN 1 ELSE 0 END), 0) AS conflicting
        FROM ranked
        """
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def finalize_database(connection: duckdb.DuckDBPyConnection) -> StorageStats:
    """Deduplicate candidates, enforce relationships, and create final tables."""

    business_identical, business_conflicting = _business_duplicate_counts(connection)
    review_identical, review_conflicting = _review_duplicate_counts(connection)

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            CREATE TABLE businesses (
                business_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                city VARCHAR,
                state VARCHAR,
                categories VARCHAR,
                source_stars DOUBLE CHECK (
                    source_stars IS NULL OR source_stars BETWEEN 1.0 AND 5.0
                ),
                source_review_count BIGINT CHECK (
                    source_review_count IS NULL OR source_review_count >= 0
                )
            );

            INSERT INTO businesses
            WITH ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY business_id ORDER BY source_line_number
                    ) AS occurrence
                FROM business_candidates
            )
            SELECT
                business_id,
                name,
                city,
                state,
                categories,
                source_stars,
                source_review_count
            FROM ranked
            WHERE occurrence = 1;

            CREATE TABLE reviews (
                review_id VARCHAR PRIMARY KEY,
                business_id VARCHAR NOT NULL,
                stars TINYINT NOT NULL CHECK (stars BETWEEN 1 AND 5),
                date DATE NOT NULL,
                FOREIGN KEY (business_id) REFERENCES businesses(business_id)
            );

            INSERT INTO reviews
            WITH ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY review_id ORDER BY source_line_number
                    ) AS occurrence
                FROM review_candidates
            )
            SELECT r.review_id, r.business_id, r.stars, r.date
            FROM ranked AS r
            INNER JOIN businesses AS b USING (business_id)
            WHERE r.occurrence = 1;
            """
        )

        orphaned_reviews = int(
            connection.execute(
                """
                WITH ranked AS (
                    SELECT
                        *,
                        row_number() OVER (
                            PARTITION BY review_id ORDER BY source_line_number
                        ) AS occurrence
                    FROM review_candidates
                )
                SELECT count(*)
                FROM ranked AS r
                LEFT JOIN businesses AS b USING (business_id)
                WHERE r.occurrence = 1 AND b.business_id IS NULL
                """
            ).fetchone()[0]
        )
        accepted_businesses = int(
            connection.execute("SELECT count(*) FROM businesses").fetchone()[0]
        )
        accepted_reviews = int(
            connection.execute("SELECT count(*) FROM reviews").fetchone()[0]
        )
        connection.execute("DROP TABLE business_candidates")
        connection.execute("DROP TABLE review_candidates")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")

    return StorageStats(
        accepted_businesses=accepted_businesses,
        identical_business_duplicates=business_identical,
        conflicting_business_duplicates=business_conflicting,
        accepted_reviews=accepted_reviews,
        identical_review_duplicates=review_identical,
        conflicting_review_duplicates=review_conflicting,
        orphaned_reviews=orphaned_reviews,
    )
