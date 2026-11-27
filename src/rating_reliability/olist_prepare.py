"""Prepare privacy-safe Olist analytical tables in DuckDB.

Relational transformations live in ``sql/olist``. This module validates source
headers, creates typed privacy-safe source tables, orchestrates transactions,
publishes outputs safely, and writes aggregate reproducibility reports.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from rating_reliability.artifacts import sha256_file, write_json_artifact
from rating_reliability.config import PROJECT_ROOT


class OlistPreparationError(Exception):
    """An expected preparation failure suitable for concise CLI display."""


HISTORICAL_CUTOFF = "2018-01-01"
VALIDATION_POLICY_VERSION = "olist-source-validation-v2"
ELIGIBILITY_POLICY_VERSION = "olist-single-seller-order-v2"
RATING_AVAILABILITY_FIELD = "review_answer_timestamp"
REPORT_NAMES = (
    "metadata.json",
    "quality_report.json",
    "population_report.json",
    "preparation_summary.json",
    "preparation_summary.md",
)
REQUIRED_FILES: dict[str, tuple[str, frozenset[str]]] = {
    "orders": (
        "olist_orders_dataset.csv",
        frozenset(
            {
                "order_id",
                "order_status",
                "order_purchase_timestamp",
                "order_approved_at",
                "order_delivered_carrier_date",
                "order_delivered_customer_date",
                "order_estimated_delivery_date",
            }
        ),
    ),
    "items": (
        "olist_order_items_dataset.csv",
        frozenset(
            {
                "order_id",
                "order_item_id",
                "product_id",
                "seller_id",
                "shipping_limit_date",
                "price",
                "freight_value",
            }
        ),
    ),
    "reviews": (
        "olist_order_reviews_dataset.csv",
        frozenset(
            {
                "review_id",
                "order_id",
                "review_score",
                "review_creation_date",
                "review_answer_timestamp",
            }
        ),
    ),
    "sellers": (
        "olist_sellers_dataset.csv",
        frozenset(
            {
                "seller_id",
                "seller_zip_code_prefix",
                "seller_city",
                "seller_state",
            }
        ),
    ),
    "products": (
        "olist_products_dataset.csv",
        frozenset({"product_id", "product_category_name"}),
    ),
}
KNOWN_OLIST_FILES = frozenset(
    {
        *(specification[0] for specification in REQUIRED_FILES.values()),
        "olist_customers_dataset.csv",
        "olist_order_payments_dataset.csv",
        "olist_geolocation_dataset.csv",
        "product_category_name_translation.csv",
    }
)


def _application_version() -> str:
    try:
        return version("rating-reliability")
    except PackageNotFoundError:
        return "unknown"


def _sql_path(name: str) -> Path:
    return PROJECT_ROOT / "sql" / "olist" / name


def _read_sql(name: str) -> str:
    path = _sql_path(name)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise OlistPreparationError(f"cannot read SQL file {path}: {error}") from error


def _quote_path(path: Path) -> str:
    return "'" + str(path.resolve()).replace("'", "''") + "'"


def _staging_database_path(destination: Path) -> Path:
    return destination.with_name(
        f".{destination.stem}.staging-{uuid4().hex}{destination.suffix}"
    )


def _staging_directory_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.staging-{uuid4().hex}")


def _read_header(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            return next(csv.reader(source))
    except StopIteration as error:
        raise OlistPreparationError(f"source file is empty: {path}") from error
    except (OSError, UnicodeError, csv.Error) as error:
        raise OlistPreparationError(
            f"cannot read source header {path}: {error}"
        ) from error


def validate_source_files(data_directory: Path) -> dict[str, Path]:
    """Validate required Olist filenames and source headers."""

    if not data_directory.is_dir():
        raise OlistPreparationError(f"data directory does not exist: {data_directory}")
    paths: dict[str, Path] = {}
    for logical_name, (filename, expected_columns) in REQUIRED_FILES.items():
        path = data_directory / filename
        if not path.is_file():
            raise OlistPreparationError(f"required source file does not exist: {path}")
        observed = _read_header(path)
        duplicates = sorted({name for name in observed if observed.count(name) > 1})
        if duplicates:
            raise OlistPreparationError(
                f"duplicate source columns in {filename}: {', '.join(duplicates)}"
            )
        missing = sorted(expected_columns - set(observed))
        if missing:
            raise OlistPreparationError(
                f"missing required columns in {filename}: {', '.join(missing)}"
            )
        paths[logical_name] = path
    return paths


def _create_source_tables(
    connection: duckdb.DuckDBPyConnection, paths: Mapping[str, Path]
) -> None:
    read = {
        name: (
            f"read_csv({_quote_path(path)}, header = true, all_varchar = true, "
            "strict_mode = true)"
        )
        for name, path in paths.items()
    }
    connection.execute(
        f"""
        CREATE TABLE source_orders AS
        SELECT
            row_number() OVER () AS source_row_number,
            nullif(trim(order_id), '') AS order_id,
            nullif(trim(order_status), '') AS order_status,
            try_cast(nullif(trim(order_purchase_timestamp), '') AS TIMESTAMP)
                AS order_purchase_timestamp,
            try_cast(nullif(trim(order_approved_at), '') AS TIMESTAMP)
                AS order_approved_at,
            try_cast(nullif(trim(order_delivered_carrier_date), '') AS TIMESTAMP)
                AS order_delivered_carrier_date,
            try_cast(nullif(trim(order_delivered_customer_date), '') AS TIMESTAMP)
                AS order_delivered_customer_date,
            try_cast(nullif(trim(order_estimated_delivery_date), '') AS TIMESTAMP)
                AS order_estimated_delivery_date,
            nullif(trim(order_id), '') IS NULL AS invalid_order_id,
            nullif(trim(order_purchase_timestamp), '') IS NULL
                OR try_cast(trim(order_purchase_timestamp) AS TIMESTAMP) IS NULL
                AS invalid_purchase_timestamp,
            nullif(trim(order_status), '') IS NULL AS missing_order_status
        FROM {read["orders"]};

        CREATE TABLE source_order_items AS
        WITH parsed AS (
            SELECT
                *,
                try_cast(nullif(trim(order_item_id), '') AS BIGINT) AS item_number,
                try_cast(nullif(trim(price), '') AS DECIMAL(18, 2)) AS parsed_price,
                try_cast(nullif(trim(freight_value), '') AS DECIMAL(18, 2))
                    AS parsed_freight
            FROM {read["items"]}
        )
        SELECT
            row_number() OVER () AS source_row_number,
            nullif(trim(order_id), '') AS order_id,
            item_number AS order_item_id,
            nullif(trim(product_id), '') AS product_id,
            nullif(trim(seller_id), '') AS seller_id,
            try_cast(nullif(trim(shipping_limit_date), '') AS TIMESTAMP)
                AS shipping_limit_timestamp,
            parsed_price AS item_value,
            parsed_freight AS freight_value,
            nullif(trim(order_id), '') IS NULL AS invalid_order_id,
            item_number IS NULL AS invalid_order_item_id,
            nullif(trim(seller_id), '') IS NULL AS invalid_seller_id,
            parsed_price IS NULL OR parsed_price < 0 AS invalid_item_value,
            parsed_freight IS NULL OR parsed_freight < 0 AS invalid_freight_value
        FROM parsed;

        CREATE TABLE source_order_reviews AS
        WITH parsed AS (
            SELECT
                *,
                try_cast(nullif(trim(review_score), '') AS DOUBLE) AS score_number,
                try_cast(nullif(trim(review_creation_date), '') AS TIMESTAMP)
                    AS parsed_review_date,
                try_cast(nullif(trim(review_answer_timestamp), '') AS TIMESTAMP)
                    AS parsed_answer_timestamp
            FROM {read["reviews"]}
        )
        SELECT
            row_number() OVER () AS source_row_number,
            nullif(trim(review_id), '') AS review_id,
            nullif(trim(order_id), '') AS order_id,
            CASE
                WHEN score_number BETWEEN 1 AND 5
                 AND score_number = trunc(score_number)
                THEN cast(score_number AS TINYINT)
            END AS review_score,
            parsed_review_date AS review_creation_date,
            parsed_answer_timestamp AS review_answer_timestamp,
            nullif(trim(review_id), '') IS NULL AS invalid_review_id,
            nullif(trim(order_id), '') IS NULL AS invalid_order_id,
            score_number IS NULL OR score_number NOT BETWEEN 1 AND 5
                OR score_number <> trunc(score_number) AS invalid_rating,
            parsed_review_date IS NULL OR NOT isfinite(parsed_review_date)
                AS invalid_review_date,
            nullif(trim(review_answer_timestamp), '') IS NULL
                AS missing_review_answer_timestamp,
            nullif(trim(review_answer_timestamp), '') IS NOT NULL
                AND (
                    parsed_answer_timestamp IS NULL
                    OR NOT isfinite(parsed_answer_timestamp)
                )
                AS invalid_review_answer_timestamp,
            coalesce(isfinite(parsed_answer_timestamp) AND isfinite(parsed_review_date)
                AND parsed_answer_timestamp < parsed_review_date, false)
                AS answer_before_review_creation
        FROM parsed;

        CREATE TABLE source_sellers AS
        SELECT
            row_number() OVER () AS source_row_number,
            nullif(trim(seller_id), '') AS seller_id,
            try_cast(nullif(trim(seller_zip_code_prefix), '') AS INTEGER)
                AS seller_zip_code_prefix,
            nullif(trim(seller_city), '') AS seller_city,
            nullif(trim(seller_state), '') AS seller_state,
            nullif(trim(seller_id), '') IS NULL AS invalid_seller_id
        FROM {read["sellers"]};

        CREATE TABLE source_products AS
        SELECT
            row_number() OVER () AS source_row_number,
            nullif(trim(product_id), '') AS product_id,
            nullif(trim(product_category_name), '') AS product_category_name
        FROM {read["products"]};
        """
    )


def _execute_transformations(connection: duckdb.DuckDBPyConnection) -> None:
    for name in (
        "01_source_validation.sql",
        "02_order_eligibility.sql",
        "03_seller_rating_observations.sql",
        "04_operational_features.sql",
        "05_historical_populations.sql",
        "06_quality_checks.sql",
    ):
        sql = _read_sql(name).replace("{{HISTORICAL_CUTOFF}}", HISTORICAL_CUTOFF)
        connection.execute(sql)


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return int(row[0] or 0)


def _validation_report(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    metrics: dict[str, dict[str, int]] = {}
    for table, metric, value in connection.execute(
        "SELECT source_table, metric, value FROM source_validation_summary "
        "ORDER BY source_table, metric"
    ).fetchall():
        metrics.setdefault(str(table), {})[str(metric)] = int(value)
    statuses = [
        {"order_status": status, "order_count": int(count)}
        for status, count in connection.execute(
            "SELECT order_status, count(*) FROM source_orders "
            "GROUP BY order_status ORDER BY order_status"
        ).fetchall()
    ]
    return {
        "source_validation": metrics,
        "order_statuses": statuses,
        "fatal_for_rating_eligibility": [
            "invalid or duplicate order identity",
            "missing or ambiguous seller relationship",
            "duplicate order-item key",
            "missing or ambiguous review relationship",
            "invalid rating",
            "invalid review creation date",
            "missing or invalid review answer timestamp",
            "review answer earlier than survey creation",
        ],
        "optional_operational_missingness": (
            "Missing delivery and optional operational timestamps do not exclude "
            "an otherwise eligible rating observation."
        ),
    }


def _eligibility_report(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    primary = [
        {"reason": reason, "order_count": int(count)}
        for reason, count in connection.execute(
            "SELECT primary_reason, count(*) FROM order_eligibility "
            "GROUP BY primary_reason ORDER BY primary_reason"
        ).fetchall()
    ]
    flag_columns = (
        "duplicate_order_id",
        "no_order_items",
        "duplicate_order_item_key",
        "invalid_seller_id",
        "multi_seller_order",
        "no_review",
        "multiple_reviews",
        "invalid_review_id",
        "non_unique_review_id",
        "invalid_rating",
        "invalid_review_date",
        "missing_review_answer_timestamp",
        "invalid_review_answer_timestamp",
        "answer_before_review_creation",
    )
    overlap = {
        column: _scalar(
            connection,
            f"SELECT count(*) FROM order_eligibility WHERE {column}",
        )
        for column in flag_columns
    }
    total = _scalar(connection, "SELECT count(*) FROM order_eligibility")
    eligible = _scalar(
        connection, "SELECT count(*) FROM order_eligibility WHERE is_eligible"
    )
    return {
        "order_eligibility_rows": total,
        "eligible_orders": eligible,
        "excluded_orders": total - eligible,
        "primary_exclusion_reasons": primary,
        "overlapping_issue_flags": overlap,
        "counting_note": (
            "Primary reasons are mutually exclusive and reconcile eligibility rows. "
            "Issue flags may overlap and must not be summed. Source rows with a blank "
            "order ID cannot enter the order-keyed eligibility table and are reported "
            "in source validation."
        ),
    }


def _population_report(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    historical = _scalar(
        connection, "SELECT count(*) FROM historical_rating_observations"
    )
    evaluation = _scalar(
        connection, "SELECT count(*) FROM evaluation_rating_observations"
    )
    final = _scalar(connection, "SELECT count(*) FROM seller_rating_observations")
    both, both_ten = connection.execute(
        """
        WITH historical AS (
            SELECT seller_id, count(*) AS ratings
            FROM historical_rating_observations GROUP BY seller_id
        ), evaluation AS (
            SELECT seller_id, count(*) AS ratings
            FROM evaluation_rating_observations GROUP BY seller_id
        )
        SELECT count(*), count(*) FILTER (WHERE h.ratings >= 10 AND e.ratings >= 10)
        FROM historical AS h JOIN evaluation AS e USING (seller_id)
        """
    ).fetchone()
    stages = [
        {
            "population": population,
            "observation_count": int(count),
            "seller_or_entity_count": int(entity_count),
        }
        for population, count, entity_count in connection.execute(
            "SELECT * FROM analytical_population_summary ORDER BY population"
        ).fetchall()
    ]
    return {
        "historical_cutoff": HISTORICAL_CUTOFF,
        "rating_availability_field": RATING_AVAILABILITY_FIELD,
        "final_rating_observations": final,
        "distinct_rated_sellers": _scalar(
            connection,
            "SELECT count(DISTINCT seller_id) FROM seller_rating_observations",
        ),
        "historical_observations": historical,
        "evaluation_observations": evaluation,
        "period_total": historical + evaluation,
        "sellers_in_both_periods": both,
        "sellers_with_at_least_10_in_each_period": both_ten,
        "stages": stages,
    }


def _operational_report(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
            count(*),
            count(actual_delivery_timestamp),
            count(estimated_delivery_timestamp),
            count(delivery_delay_days),
            count(*) FILTER (WHERE delivered_late),
            count(*) FILTER (WHERE all_item_values_valid),
            count(*) FILTER (WHERE all_freight_values_valid)
        FROM seller_operational_features
        """
    ).fetchone()
    assert row is not None
    return {
        "eligible_orders": int(row[0]),
        "orders_with_actual_delivery_timestamp": int(row[1]),
        "orders_with_estimated_delivery_timestamp": int(row[2]),
        "orders_with_computable_delivery_lateness": int(row[3]),
        "orders_delivered_late": int(row[4]),
        "orders_with_all_item_values_valid": int(row[5]),
        "orders_with_all_freight_values_valid": int(row[6]),
        "orders_with_product_category": _scalar(
            connection,
            "SELECT count(DISTINCT order_id) FROM order_product_categories",
        ),
        "order_category_relationships": _scalar(
            connection, "SELECT count(*) FROM order_product_categories"
        ),
    }


def _database_schema(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    tables = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    result: dict[str, object] = {}
    for table in sorted(str(name) for name in tables):
        result[table] = [
            {
                "column": str(row[0]),
                "type": str(row[1]),
                "nullable": str(row[2]) == "YES",
                "key": str(row[3]) or None,
            }
            for row in connection.execute(f"DESCRIBE {table}").fetchall()
        ]
    return result


def _quality_checks(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    checks = {
        str(name): int(count)
        for name, count in connection.execute(
            "SELECT check_name, failure_count FROM analytical_quality_checks "
            "ORDER BY check_name"
        ).fetchall()
    }
    failures = {name: count for name, count in checks.items() if count != 0}
    if failures:
        details = ", ".join(f"{name}={count}" for name, count in failures.items())
        raise OlistPreparationError(f"analytical integrity checks failed: {details}")
    return checks


def _input_metadata(data_directory: Path) -> list[dict[str, object]]:
    result = []
    for path in sorted(data_directory.glob("*.csv"), key=lambda value: value.name):
        result.append(
            {
                "file_name": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "used_in_analytical_database": path.name
                in {specification[0] for specification in REQUIRED_FILES.values()},
            }
        )
    return result


def _write_reports(
    connection: duckdb.DuckDBPyConnection,
    output_directory: Path,
    *,
    data_directory: Path,
    database_name: str,
) -> dict[str, Any]:
    processed_at = datetime.now(UTC).isoformat()
    validation = _validation_report(connection)
    eligibility = _eligibility_report(connection)
    population = _population_report(connection)
    operational = _operational_report(connection)
    checks = _quality_checks(connection)
    metadata: dict[str, Any] = {
        "dataset_source": "Brazilian E-Commerce Public Dataset by Olist",
        "dataset_source_url": (
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce"
        ),
        "published_license": "CC BY-NC-SA 4.0",
        "dataset_version": (
            "local Kaggle archive; release version is not encoded in the CSV files"
        ),
        "processing_timestamp_utc": processed_at,
        "application_version": _application_version(),
        "validation_policy_version": VALIDATION_POLICY_VERSION,
        "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
        "historical_cutoff": HISTORICAL_CUTOFF,
        "rating_availability_field": RATING_AVAILABILITY_FIELD,
        "database_file_name": database_name,
        "input_files": _input_metadata(data_directory),
        "pipeline_parameters": {
            "source_directory_name": data_directory.name,
            "historical_cutoff": HISTORICAL_CUTOFF,
            "overwrite_controlled_by_cli": True,
        },
        "privacy": {
            "customer_identifiers_stored": False,
            "review_text_stored": False,
            "payment_details_stored": False,
            "raw_geolocation_stored": False,
        },
    }
    summary: dict[str, Any] = {
        "report_schema_version": 2,
        "metadata": metadata,
        "validation": validation,
        "eligibility": eligibility,
        "population": population,
        "operational_coverage": operational,
        "analytical_quality_checks": checks,
        "database_schema": _database_schema(connection),
    }
    output_directory.mkdir(parents=True, exist_ok=False)
    write_json_artifact(metadata, output_directory / "metadata.json")
    write_json_artifact(
        {"validation": validation, "quality_checks": checks},
        output_directory / "quality_report.json",
    )
    write_json_artifact(
        {"eligibility": eligibility, "population": population},
        output_directory / "population_report.json",
    )
    write_json_artifact(summary, output_directory / "preparation_summary.json")
    reason_lines = "\n".join(
        f"- `{row['reason']}`: {row['order_count']:,}"
        for row in eligibility["primary_exclusion_reasons"]
    )
    historical_count = population["historical_observations"]
    evaluation_count = population["evaluation_observations"]
    both_periods = population["sellers_in_both_periods"]
    both_ten = population["sellers_with_at_least_10_in_each_period"]
    delivery_count = operational["orders_with_computable_delivery_lateness"]
    markdown = f"""# Olist Analytical Preparation Summary

Generated locally at {processed_at}.

## Population

- Source order rows: {validation["source_validation"]["orders"]["row_count"]:,}
- Eligible rating observations: {population["final_rating_observations"]:,}
- Distinct rated sellers: {population["distinct_rated_sellers"]:,}
- Historical observations answered before {HISTORICAL_CUTOFF}: {historical_count:,}
- Evaluation observations answered on/after {HISTORICAL_CUTOFF}: {evaluation_count:,}
- Sellers in both periods: {both_periods:,}
- Sellers with at least 10 ratings in each period: {both_ten:,}

## Primary eligibility outcomes

{reason_lines}

Primary reasons are mutually exclusive. Overlapping flags in
`population_report.json` must not be summed.

## Operational coverage

- Computable delivery lateness: {delivery_count:,}
- Product category available: {operational["orders_with_product_category"]:,}
- All item values valid: {operational["orders_with_all_item_values_valid"]:,}
- All freight values valid: {operational["orders_with_all_freight_values_valid"]:,}

Delivery outcomes become known after purchase and must not be used as historical
predictors before their availability. Ratings remain order-experience measures,
not objective seller quality.

## Privacy and publication

The database excludes customer identifiers, review text, payment details, and
raw geolocation. Original CSVs and these derived outputs remain local. Public
publication is conditional on the documented CC BY-NC-SA 4.0 requirements.
"""
    (output_directory / "preparation_summary.md").write_text(
        markdown, encoding="utf-8", newline="\n"
    )
    return summary


def _record_pipeline_metadata(
    connection: duckdb.DuckDBPyConnection, summary: Mapping[str, Any]
) -> None:
    metadata = summary["metadata"]
    connection.execute(
        """
        CREATE TABLE pipeline_metadata (
            metadata_key VARCHAR PRIMARY KEY,
            metadata_value VARCHAR NOT NULL
        )
        """
    )
    rows = [
        ("application_version", metadata["application_version"]),
        ("validation_policy_version", VALIDATION_POLICY_VERSION),
        ("eligibility_policy_version", ELIGIBILITY_POLICY_VERSION),
        ("historical_cutoff", HISTORICAL_CUTOFF),
        ("rating_availability_field", RATING_AVAILABILITY_FIELD),
        ("processing_timestamp_utc", metadata["processing_timestamp_utc"]),
        ("input_files_json", json.dumps(metadata["input_files"], sort_keys=True)),
    ]
    connection.executemany("INSERT INTO pipeline_metadata VALUES (?, ?)", rows)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _publish_outputs(
    staged_database: Path,
    staged_reports: Path,
    database: Path,
    reports: Path,
    *,
    overwrite: bool,
) -> None:
    if not overwrite and (database.exists() or reports.exists()):
        raise OlistPreparationError(
            "output exists; use --overwrite to replace the database and reports"
        )
    database_backup = database.with_name(f".{database.name}.backup-{uuid4().hex}")
    reports_backup = reports.with_name(f".{reports.name}.backup-{uuid4().hex}")
    database_published = False
    reports_published = False
    try:
        if database.exists():
            os.replace(database, database_backup)
        if reports.exists():
            os.replace(reports, reports_backup)
        os.replace(staged_database, database)
        database_published = True
        os.replace(staged_reports, reports)
        reports_published = True
    except OSError as error:
        if reports_published:
            _remove_path(reports)
        if database_published:
            _remove_path(database)
        if database_backup.exists():
            os.replace(database_backup, database)
        if reports_backup.exists():
            os.replace(reports_backup, reports)
        raise OlistPreparationError(
            f"cannot publish prepared outputs: {error}"
        ) from error
    finally:
        _remove_path(database_backup)
        _remove_path(reports_backup)


def prepare_olist(
    *,
    data_directory: Path,
    database: Path,
    output_directory: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build and safely publish the Olist analytical database and reports."""

    paths = validate_source_files(data_directory)
    if not overwrite and (database.exists() or output_directory.exists()):
        raise OlistPreparationError(
            "output exists; use --overwrite to replace the database and reports"
        )
    database.parent.mkdir(parents=True, exist_ok=True)
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    staged_database = _staging_database_path(database)
    staged_reports = _staging_directory_path(output_directory)
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(staged_database))
        connection.execute("BEGIN TRANSACTION")
        _create_source_tables(connection, paths)
        _execute_transformations(connection)
        summary = _write_reports(
            connection,
            staged_reports,
            data_directory=data_directory,
            database_name=database.name,
        )
        _record_pipeline_metadata(connection, summary)
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")
        connection.close()
        connection = None
        _publish_outputs(
            staged_database,
            staged_reports,
            database,
            output_directory,
            overwrite=overwrite,
        )
        return summary
    except OlistPreparationError:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
        raise
    except (duckdb.Error, OSError, ValueError) as error:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except duckdb.Error:
                pass
        raise OlistPreparationError(f"Olist preparation failed: {error}") from error
    finally:
        if connection is not None:
            connection.close()
        _remove_path(staged_database)
        _remove_path(staged_reports)


def build_parser() -> argparse.ArgumentParser:
    """Build the Olist preparation CLI parser."""

    parser = argparse.ArgumentParser(
        description="Build validated Olist analytical tables and population reports."
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/local/olist_preparation"),
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the preparation CLI and return a conventional process exit code."""

    args = build_parser().parse_args(argv)
    try:
        summary = prepare_olist(
            data_directory=args.data_dir,
            database=args.database,
            output_directory=args.output_dir,
            overwrite=args.overwrite,
        )
    except OlistPreparationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    population = summary["population"]
    print(f"eligible rating observations: {population['final_rating_observations']}")
    print(f"distinct rated sellers: {population['distinct_rated_sellers']}")
    print(f"historical observations: {population['historical_observations']}")
    print(f"evaluation observations: {population['evaluation_observations']}")
    print(f"database: {args.database}")
    print(f"reports: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
