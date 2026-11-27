"""Synthetic integration tests for Olist analytical preparation."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from rating_reliability.olist_prepare import (
    OlistPreparationError,
    main,
    prepare_olist,
)


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)


def _source_files(tmp_path: Path) -> Path:
    data = tmp_path / "olist"
    data.mkdir(parents=True)
    order_rows = []
    for number in range(1, 12):
        order_id = f"o{number}"
        purchase = "2017-12-20 10:00:00" if number in (1, 11) else "2018-01-02 10:00:00"
        actual = "" if number == 1 else "2018-01-12 10:00:00"
        estimated = "2017-12-30 10:00:00" if number == 1 else "2018-01-10 10:00:00"
        order_rows.append(
            [
                order_id,
                "customer-private",
                "delivered",
                purchase,
                purchase,
                purchase,
                actual,
                estimated,
            ]
        )
    _write_csv(
        data / "olist_orders_dataset.csv",
        [
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        order_rows,
    )
    _write_csv(
        data / "olist_order_items_dataset.csv",
        [
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ],
        [
            ["o1", 1, "p1", "s1", "2017-12-22", 10, 2],
            ["o1", 2, "p2", "s1", "2017-12-22", 20, 3],
            ["o2", 1, "p1", "s1", "2018-01-03", 40, 4],
            ["o3", 1, "p1", "s1", "2018-01-03", 10, 1],
            ["o3", 2, "p2", "s2", "2018-01-03", 20, 2],
            ["o5", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o6", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o7", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o8", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o9", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o10", 1, "p1", "s2", "2018-01-03", 10, 1],
            ["o11", 1, "p2", "s2", "2018-01-03", 15, 2],
        ],
    )
    review_rows = [
        ["r1", "o1", 5, "title", "PRIVATE TEXT", "2017-12-31", "2018-01-01"],
        ["r2", "o2", 4, "", "", "2018-01-01", "2018-01-02"],
        ["r3", "o3", 3, "", "", "2018-01-03", "2018-01-04"],
        ["r4", "o4", 2, "", "", "2018-01-03", "2018-01-04"],
        ["r6a", "o6", 4, "", "", "2018-01-03", "2018-01-04"],
        ["r6b", "o6", 5, "", "", "2018-01-04", "2018-01-05"],
        ["shared", "o7", 4, "", "", "2018-01-03", "2018-01-04"],
        ["shared", "o8", 2, "", "", "2018-01-03", "2018-01-04"],
        ["r9", "o9", 6, "", "", "2018-01-03", "2018-01-04"],
        ["r10", "o10", 3, "", "", "not-a-date", "2018-01-04"],
        ["r11", "o11", 1, "", "", "2017-12-30", "2017-12-31"],
    ]
    _write_csv(
        data / "olist_order_reviews_dataset.csv",
        [
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ],
        review_rows,
    )
    _write_csv(
        data / "olist_sellers_dataset.csv",
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
        [["s1", 1, "City", "SP"], ["s2", 2, "Town", "RJ"]],
    )
    _write_csv(
        data / "olist_products_dataset.csv",
        [
            "product_id",
            "product_category_name",
            "product_name_lenght",
        ],
        [["p1", "category_a", 10], ["p2", "category_b", 12]],
    )
    return data


def _prepare(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    data = _source_files(tmp_path)
    database = tmp_path / "olist.duckdb"
    reports = tmp_path / "reports"
    summary = prepare_olist(
        data_directory=data,
        database=database,
        output_directory=reports,
    )
    return summary, database, reports


def test_preparation_materializes_expected_tables_and_populations(
    tmp_path: Path,
) -> None:
    summary, database, _ = _prepare(tmp_path)

    assert summary["population"]["final_rating_observations"] == 3
    assert summary["population"]["distinct_rated_sellers"] == 2
    assert summary["population"]["historical_observations"] == 1
    assert summary["population"]["evaluation_observations"] == 2
    with duckdb.connect(str(database), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert {
            "source_orders",
            "source_order_items",
            "source_order_reviews",
            "source_sellers",
            "order_eligibility",
            "seller_rating_observations",
            "seller_operational_features",
            "order_product_categories",
            "analytical_population_summary",
            "analytical_quality_checks",
            "pipeline_metadata",
        } <= tables
        assert connection.execute(
            "SELECT order_id FROM historical_rating_observations"
        ).fetchall() == [("o11",)]
        assert connection.execute(
            "SELECT order_id FROM evaluation_rating_observations ORDER BY order_id"
        ).fetchall() == [("o1",), ("o2",)]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("", "MISSING_REVIEW_ANSWER_TIMESTAMP"),
        ("bad timestamp", "INVALID_REVIEW_ANSWER_TIMESTAMP"),
        ("infinity", "INVALID_REVIEW_ANSWER_TIMESTAMP"),
        ("2017-12-30 23:59:59", "ANSWER_BEFORE_REVIEW_CREATION"),
    ],
)
def test_unavailable_rating_answers_are_explicitly_excluded(
    tmp_path: Path, answer: str, reason: str
) -> None:
    data = _source_files(tmp_path)
    reviews = data / "olist_order_reviews_dataset.csv"
    with reviews.open(encoding="utf-8", newline="") as source:
        columns, *rows = csv.reader(source)
    rows[0][-1] = answer
    _write_csv(reviews, columns, rows)
    database = tmp_path / "olist.duckdb"
    summary = prepare_olist(
        data_directory=data, database=database, output_directory=tmp_path / "reports"
    )
    assert summary["population"]["final_rating_observations"] == 2
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute(
            "SELECT primary_reason FROM order_eligibility WHERE order_id = 'o1'"
        ).fetchone() == (reason,)
        assert connection.execute(
            "SELECT count(*) FROM seller_rating_observations WHERE order_id = 'o1'"
        ).fetchone() == (0,)


def test_eligibility_reasons_are_explicit_and_reconcile(tmp_path: Path) -> None:
    summary, database, _ = _prepare(tmp_path)
    reasons = {
        row["reason"]: row["order_count"]
        for row in summary["eligibility"]["primary_exclusion_reasons"]
    }

    assert reasons == {
        "ELIGIBLE": 3,
        "INVALID_RATING": 1,
        "INVALID_REVIEW_DATE": 1,
        "MULTIPLE_REVIEWS": 1,
        "MULTI_SELLER_ORDER": 1,
        "NON_UNIQUE_REVIEW_ID": 2,
        "NO_ORDER_ITEMS": 1,
        "NO_REVIEW": 1,
    }
    assert sum(reasons.values()) == 11
    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*), count(DISTINCT order_id) FROM seller_rating_observations"
        ).fetchone() == (3, 3)
        assert connection.execute(
            "SELECT count(*) FROM analytical_quality_checks WHERE failure_count <> 0"
        ).fetchone() == (0,)


def test_operational_aggregates_and_categories_do_not_duplicate_values(
    tmp_path: Path,
) -> None:
    _, database, _ = _prepare(tmp_path)

    with duckdb.connect(str(database), read_only=True) as connection:
        o1 = connection.execute(
            """
            SELECT item_count, distinct_product_count, total_item_value,
                   total_freight_value, delivery_delay_days, delivered_late
            FROM seller_operational_features WHERE order_id = 'o1'
            """
        ).fetchone()
        categories = connection.execute(
            "SELECT product_category FROM order_product_categories "
            "WHERE order_id = 'o1' ORDER BY product_category"
        ).fetchall()

    assert o1 == (2, 2, pytest.approx(30), pytest.approx(5), None, None)
    assert categories == [("category_a",), ("category_b",)]


def test_source_validation_reports_duplicates_and_broken_relationships(
    tmp_path: Path,
) -> None:
    data = _source_files(tmp_path)
    orders = data / "olist_orders_dataset.csv"
    sellers = data / "olist_sellers_dataset.csv"
    with orders.open("r", encoding="utf-8", newline="") as source:
        order_rows = list(csv.reader(source))
    with orders.open("a", encoding="utf-8", newline="") as output:
        csv.writer(output).writerow(order_rows[1])
    with sellers.open("a", encoding="utf-8", newline="") as output:
        csv.writer(output).writerow(["s1", 9, "Other", "SP"])

    summary = prepare_olist(
        data_directory=data,
        database=tmp_path / "duplicate.duckdb",
        output_directory=tmp_path / "duplicate-reports",
    )

    validation = summary["validation"]["source_validation"]
    assert validation["orders"]["duplicate_order_id_excess_rows"] == 1
    assert validation["sellers"]["duplicate_seller_id_excess_rows"] == 1
    assert summary["eligibility"]["overlapping_issue_flags"]["duplicate_order_id"] == 1
    assert summary["eligibility"]["overlapping_issue_flags"]["invalid_seller_id"] >= 1


def test_missing_and_invalid_source_schemas_fail_before_publication(
    tmp_path: Path,
) -> None:
    data = _source_files(tmp_path)
    (data / "olist_sellers_dataset.csv").unlink()
    with pytest.raises(OlistPreparationError, match="required source file"):
        prepare_olist(
            data_directory=data,
            database=tmp_path / "missing.duckdb",
            output_directory=tmp_path / "missing-reports",
        )

    data = _source_files(tmp_path / "second")
    _write_csv(
        data / "olist_products_dataset.csv",
        ["product_id"],
        [["p1"]],
    )
    with pytest.raises(OlistPreparationError, match="missing required columns"):
        prepare_olist(
            data_directory=data,
            database=tmp_path / "schema.duckdb",
            output_directory=tmp_path / "schema-reports",
        )


def test_reports_are_privacy_safe_and_include_reproducibility_metadata(
    tmp_path: Path,
) -> None:
    _, _, reports = _prepare(tmp_path)
    combined = "".join(path.read_text(encoding="utf-8") for path in reports.iterdir())
    metadata = json.loads((reports / "metadata.json").read_text(encoding="utf-8"))

    assert "PRIVATE TEXT" not in combined
    assert "customer-private" not in combined
    assert metadata["historical_cutoff"] == "2018-01-01"
    assert metadata["rating_availability_field"] == "review_answer_timestamp"
    assert metadata["validation_policy_version"]
    assert metadata["eligibility_policy_version"]
    assert len(metadata["input_files"]) == 5
    assert all(row["sha256"] for row in metadata["input_files"])


def test_repeat_run_safety_and_failed_run_preserve_outputs(tmp_path: Path) -> None:
    data = _source_files(tmp_path)
    database = tmp_path / "safe.duckdb"
    reports = tmp_path / "safe-reports"
    prepare_olist(data_directory=data, database=database, output_directory=reports)
    before_database = hashlib.sha256(database.read_bytes()).hexdigest()
    before_report = (reports / "population_report.json").read_bytes()

    with pytest.raises(OlistPreparationError, match="--overwrite"):
        prepare_olist(
            data_directory=data,
            database=database,
            output_directory=reports,
        )
    with (data / "olist_order_reviews_dataset.csv").open(
        "a", encoding="utf-8", newline=""
    ) as output:
        output.write('\n"unterminated')
    with pytest.raises(OlistPreparationError, match="preparation failed"):
        prepare_olist(
            data_directory=data,
            database=database,
            output_directory=reports,
            overwrite=True,
        )

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_database
    assert (reports / "population_report.json").read_bytes() == before_report


def test_explicit_overwrite_rebuilds_without_accumulating_rows(tmp_path: Path) -> None:
    data = _source_files(tmp_path)
    database = tmp_path / "repeat.duckdb"
    reports = tmp_path / "repeat-reports"
    prepare_olist(data_directory=data, database=database, output_directory=reports)
    prepare_olist(
        data_directory=data,
        database=database,
        output_directory=reports,
        overwrite=True,
    )

    with duckdb.connect(str(database), read_only=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM seller_rating_observations"
        ).fetchone() == (3,)


def test_cli_success_and_ordinary_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _source_files(tmp_path)
    database = tmp_path / "cli.duckdb"
    reports = tmp_path / "cli-reports"

    assert (
        main(
            [
                "--data-dir",
                str(data),
                "--database",
                str(database),
                "--output-dir",
                str(reports),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "eligible rating observations: 3" in captured.out

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path / "absent"),
                "--database",
                str(tmp_path / "x.duckdb"),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "data directory does not exist" in captured.err
    assert "Traceback" not in captured.err
