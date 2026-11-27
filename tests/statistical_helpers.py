"""Synthetic fixtures shared by Phase 3B statistical tests."""

from __future__ import annotations

from pathlib import Path

import duckdb


def create_statistical_database(path: Path) -> Path:
    """Create a minimal Phase 2C-shaped database with no private source data."""

    rows = [
        ("h1", "s1", 1, "2017-01-10"),
        ("h2", "s1", 5, "2017-06-10"),
        ("h3", "s2", 2, "2017-02-10"),
        ("h4", "s2", 4, "2017-06-11"),
        ("v1", "s1", 4, "2017-07-10"),
        ("v2", "s2", 3, "2017-08-10"),
        ("t1", "s1", 5, "2017-10-10"),
        ("t2", "s2", 2, "2017-11-10"),
        ("e1", "s1", 5, "2018-01-01"),
        ("e2", "s1", 4, "2018-02-10"),
        ("e3", "s2", 2, "2018-01-11"),
        ("e4", "s2", 3, "2018-02-11"),
    ]
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE seller_rating_observations (
                order_id VARCHAR PRIMARY KEY,
                seller_id VARCHAR NOT NULL,
                rating TINYINT NOT NULL,
                review_creation_date TIMESTAMP NOT NULL,
                review_answer_timestamp TIMESTAMP NOT NULL
            )
            """
        )
        earlier_creation = {
            "v1": "2017-06-30",
            "t1": "2017-09-30",
            "e1": "2017-12-31",
            "e4": "2018-01-21",
        }
        connection.executemany(
            "INSERT INTO seller_rating_observations VALUES (?, ?, ?, ?, ?)",
            [
                (order, seller, rating, earlier_creation.get(order, answered), answered)
                for order, seller, rating, answered in rows
            ],
        )
        connection.execute(
            """
            CREATE VIEW historical_rating_observations AS
            SELECT * FROM seller_rating_observations
            WHERE review_answer_timestamp < DATE '2018-01-01'
            """
        )
        connection.execute(
            """
            CREATE VIEW evaluation_rating_observations AS
            SELECT * FROM seller_rating_observations
            WHERE review_answer_timestamp >= DATE '2018-01-01'
            """
        )
        connection.execute(
            "CREATE TABLE pipeline_metadata "
            "(metadata_key VARCHAR PRIMARY KEY, metadata_value VARCHAR NOT NULL)"
        )
        connection.execute(
            "INSERT INTO pipeline_metadata VALUES ('historical_cutoff', '2018-01-01'), "
            "('rating_availability_field', 'review_answer_timestamp')"
        )
        connection.execute(
            "CREATE TABLE analytical_quality_checks "
            "(check_name VARCHAR, failure_count BIGINT)"
        )
        connection.execute(
            "INSERT INTO analytical_quality_checks VALUES ('synthetic_ok', 0)"
        )
    return path


def write_fast_protocol(source: Path, destination: Path) -> Path:
    """Write a test-only computationally small form of the locked protocol."""

    text = source.read_text(encoding="utf-8")
    replacements = {
        "posterior_draws = 20000": "posterior_draws = 200",
        "bootstrap_resamples = 2000": "bootstrap_resamples = 100",
        "ranking_top_k = [10, 25, 50]": "ranking_top_k = [1, 2]",
        "minimum_sellers_per_fold = 50": "minimum_sellers_per_fold = 1",
        "minimum_seller_fold_units = 200": "minimum_seller_fold_units = 2",
    }
    for old, new in replacements.items():
        if old not in text:
            raise AssertionError(f"locked test replacement not found: {old}")
        text = text.replace(old, new)
    destination.write_text(text, encoding="utf-8", newline="\n")
    return destination
