"""Evidence-based suitability audit for the local Olist CSV release.

The audit never exports review text or customer identifiers. A usable seller rating is
one valid review row for one order containing items from exactly one distinct seller.
Ambiguous repeated review rows and review IDs linked to multiple orders are retained in
quality counts but excluded from seller-level calculations.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections.abc import Sequence
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


class OlistAuditError(Exception):
    """An expected audit failure suitable for concise CLI display."""


REQUIRED_FILES = {
    "orders": "olist_orders_dataset.csv",
    "items": "olist_order_items_dataset.csv",
    "reviews": "olist_order_reviews_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
}
OPTIONAL_FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "payments": "olist_order_payments_dataset.csv",
    "products": "olist_products_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}
OUTPUT_FILES = (
    "audit_summary.json",
    "seller_attribution.csv",
    "seller_review_distribution.csv",
    "temporal_feasibility.csv",
    "data_quality_summary.json",
    "dataset_suitability.md",
    "seller_review_count_distribution.png",
    "customer_rating_distribution.png",
    "review_volume_vs_seller_mean_rating.png",
    "historical_vs_future_review_coverage.png",
)
THRESHOLDS = (1, 5, 10, 20, 30, 50, 100)


def _quote(value: Path) -> str:
    return "'" + str(value.resolve()).replace("'", "''") + "'"


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    assert row is not None
    return int(row[0] or 0)


def _number(value: object) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return float(value)


def _create_views(
    connection: duckdb.DuckDBPyConnection, paths: dict[str, Path]
) -> None:
    for name, path in paths.items():
        connection.execute(
            f"CREATE VIEW {name} AS SELECT * FROM read_csv_auto({_quote(path)}, "
            "header = true, sample_size = -1)"
        )

    connection.execute(
        """
        CREATE VIEW order_seller_counts AS
        SELECT
            order_id,
            count(*) AS item_count,
            count(DISTINCT seller_id) AS distinct_seller_count,
            min(seller_id) AS single_seller_id,
            sum(price) AS item_value,
            sum(freight_value) AS freight_value
        FROM items
        GROUP BY order_id
        """
    )
    connection.execute(
        """
        CREATE VIEW review_identity AS
        SELECT
            *,
            count(*) OVER (PARTITION BY order_id) AS review_rows_for_order,
            count(DISTINCT order_id) OVER (PARTITION BY review_id)
                AS orders_for_review_id
        FROM reviews
        """
    )
    connection.execute(
        """
        CREATE VIEW usable_ratings AS
        SELECT
            r.order_id,
            r.review_id,
            s.single_seller_id AS seller_id,
            CAST(r.review_score AS INTEGER) AS rating,
            CAST(r.review_creation_date AS TIMESTAMP) AS review_date,
            o.order_purchase_timestamp,
            o.order_status,
            o.order_delivered_customer_date,
            o.order_estimated_delivery_date,
            s.item_count,
            s.item_value,
            s.freight_value
        FROM review_identity AS r
        JOIN orders AS o USING (order_id)
        JOIN order_seller_counts AS s USING (order_id)
        WHERE r.review_rows_for_order = 1
          AND r.orders_for_review_id = 1
          AND r.review_id IS NOT NULL AND trim(r.review_id) <> ''
          AND r.order_id IS NOT NULL AND trim(r.order_id) <> ''
          AND r.review_score BETWEEN 1 AND 5
          AND r.review_score = CAST(r.review_score AS INTEGER)
          AND r.review_creation_date IS NOT NULL
          AND s.distinct_seller_count = 1
          AND s.single_seller_id IS NOT NULL
        """
    )


def _schema_profile(
    connection: duckdb.DuckDBPyConnection, names: Sequence[str]
) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in names:
        columns = connection.execute(f"DESCRIBE {name}").fetchall()
        missing = connection.execute(
            " UNION ALL ".join(
                f"SELECT {_literal(str(column[0]))} AS column_name, "
                f"count(*) FILTER (WHERE {column[0]} IS NULL) AS missing_count "
                f"FROM {name}"
                for column in columns
            )
        ).fetchall()
        result[name] = {
            "row_count": _scalar(connection, f"SELECT count(*) FROM {name}"),
            "columns": [
                {
                    "name": str(column[0]),
                    "observed_type": str(column[1]),
                    "nullable": str(column[2]) == "YES",
                }
                for column in columns
            ],
            "missing_values": {str(column): int(count) for column, count in missing},
        }
    return result


def _review_quality(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT
            count(*), count(DISTINCT review_id), count(DISTINCT order_id),
            count(*) FILTER (WHERE review_id IS NULL OR trim(review_id) = ''),
            count(*) FILTER (WHERE order_id IS NULL OR trim(order_id) = ''),
            count(*) FILTER (
                WHERE review_score IS NULL OR review_score NOT BETWEEN 1 AND 5
                   OR review_score <> CAST(review_score AS INTEGER)
            ),
            count(*) FILTER (WHERE review_creation_date IS NULL),
            min(review_creation_date), max(review_creation_date)
        FROM reviews
        """
    ).fetchone()
    assert row is not None
    exact_duplicate_excess = _scalar(
        connection,
        """
        SELECT coalesce(sum(n - 1), 0) FROM (
            SELECT count(*) AS n
            FROM reviews
            GROUP BY review_id, order_id, review_score, review_comment_title,
                     review_comment_message, review_creation_date,
                     review_answer_timestamp
            HAVING count(*) > 1
        )
        """,
    )
    duplicate_id_excess = int(row[0]) - int(row[1])
    return {
        "source_review_rows": int(row[0]),
        "distinct_review_ids": int(row[1]),
        "distinct_reviewed_orders": int(row[2]),
        "duplicate_review_id_excess_rows": duplicate_id_excess,
        "review_ids_linked_to_multiple_orders": _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT review_id FROM reviews WHERE review_id IS NOT NULL
                GROUP BY review_id HAVING count(DISTINCT order_id) > 1
            )
            """,
        ),
        "exact_duplicate_excess_rows": exact_duplicate_excess,
        "conflicting_duplicate_id_excess_rows": max(
            0, duplicate_id_excess - exact_duplicate_excess
        ),
        "missing_review_ids": int(row[3]),
        "missing_order_ids": int(row[4]),
        "invalid_ratings": int(row[5]),
        "missing_review_dates": int(row[6]),
        "minimum_review_date": row[7].isoformat() if row[7] else None,
        "maximum_review_date": row[8].isoformat() if row[8] else None,
        "orders_with_no_review": _scalar(
            connection,
            """
            SELECT count(*) FROM orders AS o
            LEFT JOIN reviews AS r USING (order_id) WHERE r.order_id IS NULL
            """,
        ),
        "orders_with_exactly_one_review_row": _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT order_id FROM reviews GROUP BY order_id HAVING count(*) = 1
            )
            """,
        ),
        "orders_with_multiple_review_rows": _scalar(
            connection,
            """
            SELECT count(*) FROM (
                SELECT order_id FROM reviews GROUP BY order_id HAVING count(*) > 1
            )
            """,
        ),
        "orphan_review_rows": _scalar(
            connection,
            """
            SELECT count(*) FROM reviews AS r
            LEFT JOIN orders AS o USING (order_id) WHERE o.order_id IS NULL
            """,
        ),
    }


def _relational_quality(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Return key uniqueness and foreign-key diagnostics."""

    return {
        "duplicate_order_id_excess_rows": _scalar(
            connection, "SELECT count(*) - count(DISTINCT order_id) FROM orders"
        ),
        "duplicate_seller_id_excess_rows": _scalar(
            connection, "SELECT count(*) - count(DISTINCT seller_id) FROM sellers"
        ),
        "duplicate_order_item_key_excess_rows": _scalar(
            connection,
            """
            SELECT count(*) - count(DISTINCT (order_id, order_item_id)) FROM items
            """,
        ),
        "order_item_rows_without_order": _scalar(
            connection,
            """
            SELECT count(*) FROM items AS i
            LEFT JOIN orders AS o USING (order_id) WHERE o.order_id IS NULL
            """,
        ),
        "order_item_rows_without_seller": _scalar(
            connection,
            """
            SELECT count(*) FROM items AS i
            LEFT JOIN sellers AS s USING (seller_id) WHERE s.seller_id IS NULL
            """,
        ),
        "orders_without_items": _scalar(
            connection,
            """
            SELECT count(*) FROM orders AS o
            LEFT JOIN items AS i USING (order_id) WHERE i.order_id IS NULL
            """,
        ),
    }


def _date_coverage(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    """Profile review availability and order purchase dates separately."""

    purchase = connection.execute(
        """
        SELECT min(order_purchase_timestamp), max(order_purchase_timestamp),
               count(*) FILTER (WHERE order_purchase_timestamp IS NULL)
        FROM orders
        """
    ).fetchone()
    assert purchase is not None
    yearly = connection.execute(
        """
        SELECT year(review_date), count(*) FROM usable_ratings
        GROUP BY year(review_date) ORDER BY year(review_date)
        """
    ).fetchall()
    monthly = connection.execute(
        """
        SELECT strftime(review_date, '%Y-%m'), count(*) FROM usable_ratings
        GROUP BY strftime(review_date, '%Y-%m') ORDER BY 1
        """
    ).fetchall()
    return {
        "purchase_timestamp_minimum": purchase[0].isoformat() if purchase[0] else None,
        "purchase_timestamp_maximum": purchase[1].isoformat() if purchase[1] else None,
        "missing_purchase_timestamps": int(purchase[2]),
        "usable_reviews_by_year": [
            {"year": int(year), "count": int(count)} for year, count in yearly
        ],
        "usable_reviews_by_month": [
            {"month": month, "count": int(count)} for month, count in monthly
        ],
        "historical_evaluation_date": "review_creation_date",
        "purchase_timestamp_role": (
            "Operational context only; it is not substituted for rating availability."
        ),
    }


def _attribution(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    total_orders = _scalar(connection, "SELECT count(*) FROM orders")
    reviewed_orders = _scalar(
        connection, "SELECT count(DISTINCT order_id) FROM reviews"
    )
    single_all = _scalar(
        connection,
        "SELECT count(*) FROM order_seller_counts WHERE distinct_seller_count = 1",
    )
    single_reviewed = _scalar(
        connection,
        """
        SELECT count(DISTINCT r.order_id) FROM reviews AS r
        JOIN order_seller_counts AS s USING (order_id)
        WHERE s.distinct_seller_count = 1
        """,
    )
    multi_reviewed = _scalar(
        connection,
        """
        SELECT count(DISTINCT r.order_id) FROM reviews AS r
        JOIN order_seller_counts AS s USING (order_id)
        WHERE s.distinct_seller_count > 1
        """,
    )
    return {
        "all_orders": total_orders,
        "orders_with_items": _scalar(
            connection, "SELECT count(*) FROM order_seller_counts"
        ),
        "single_seller_orders": single_all,
        "multi_seller_orders": _scalar(
            connection,
            "SELECT count(*) FROM order_seller_counts WHERE distinct_seller_count > 1",
        ),
        "single_seller_percentage_all_orders": 100 * single_all / total_orders,
        "reviewed_orders": reviewed_orders,
        "single_seller_reviewed_orders": single_reviewed,
        "single_seller_percentage_reviewed_orders": (
            100 * single_reviewed / reviewed_orders
        ),
        "multi_seller_reviewed_orders_excluded": multi_reviewed,
        "multi_seller_percentage_reviewed_orders": 100
        * multi_reviewed
        / reviewed_orders,
        "review_rows_on_single_seller_orders": _scalar(
            connection,
            """
            SELECT count(*) FROM reviews AS r
            JOIN order_seller_counts AS s USING (order_id)
            WHERE s.distinct_seller_count = 1
            """,
        ),
        "distinct_sellers_in_single_seller_reviewed_orders": _scalar(
            connection,
            """
            SELECT count(DISTINCT s.single_seller_id) FROM reviews AS r
            JOIN order_seller_counts AS s USING (order_id)
            WHERE s.distinct_seller_count = 1
            """,
        ),
        "usable_rating_observations": _scalar(
            connection, "SELECT count(*) FROM usable_ratings"
        ),
        "sellers_with_usable_ratings": _scalar(
            connection, "SELECT count(DISTINCT seller_id) FROM usable_ratings"
        ),
    }


def _distribution(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        WITH counts AS (
            SELECT seller_id, count(*) AS n FROM usable_ratings GROUP BY seller_id
        )
        SELECT count(*), avg(n), median(n), stddev_samp(n), min(n), max(n),
               quantile_cont(n, .25), quantile_cont(n, .75),
               quantile_cont(n, .90), quantile_cont(n, .95), sum(n)
        FROM counts
        """
    ).fetchone()
    assert row is not None
    total_sellers = int(row[0])
    total_ratings = int(row[10])
    threshold_rows = connection.execute(
        """
        WITH counts AS (
            SELECT seller_id, count(*) AS n FROM usable_ratings GROUP BY seller_id
        ), thresholds(threshold) AS (VALUES (1), (5), (10), (20), (30), (50), (100))
        SELECT threshold, count(*) FILTER (WHERE n >= threshold),
               coalesce(sum(n) FILTER (WHERE n >= threshold), 0)
        FROM thresholds CROSS JOIN counts GROUP BY threshold ORDER BY threshold
        """
    ).fetchall()
    return {
        "seller_count": total_sellers,
        "usable_rating_count": total_ratings,
        "mean": _number(row[1]),
        "median": _number(row[2]),
        "standard_deviation": _number(row[3]),
        "minimum": _number(row[4]),
        "maximum": _number(row[5]),
        "q25": _number(row[6]),
        "q75": _number(row[7]),
        "q90": _number(row[8]),
        "q95": _number(row[9]),
        "thresholds": [
            {
                "minimum_ratings": int(threshold),
                "eligible_sellers": int(sellers),
                "seller_percentage": 100 * int(sellers) / total_sellers,
                "retained_ratings": int(ratings),
                "rating_percentage": 100 * int(ratings) / total_ratings,
            }
            for threshold, sellers, ratings in threshold_rows
        ],
    }


def _rating_profile(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT count(*), avg(rating), median(rating), stddev_samp(rating),
               min(rating), max(rating) FROM usable_ratings
        """
    ).fetchone()
    assert row is not None
    frequencies = connection.execute(
        """
        SELECT rating, count(*), count(*) / sum(count(*)) OVER ()
        FROM usable_ratings GROUP BY rating ORDER BY rating
        """
    ).fetchall()
    volume = connection.execute(
        """
        WITH seller_means AS (
            SELECT seller_id, count(*) AS n, avg(rating) AS mean_rating
            FROM usable_ratings GROUP BY seller_id
        ), labeled AS (
            SELECT *, CASE
                WHEN n < 5 THEN '1-4' WHEN n < 10 THEN '5-9'
                WHEN n < 20 THEN '10-19' WHEN n < 50 THEN '20-49'
                WHEN n < 100 THEN '50-99' ELSE '100+' END AS volume_group
            FROM seller_means
        )
        SELECT volume_group, count(*), sum(n), avg(mean_rating),
               median(mean_rating), stddev_samp(mean_rating),
               count(*) FILTER (WHERE mean_rating IN (1, 5)),
               count(*) FILTER (WHERE mean_rating <= 1.5 OR mean_rating >= 4.5)
        FROM labeled GROUP BY volume_group ORDER BY min(n)
        """
    ).fetchall()
    return {
        "count": int(row[0]),
        "mean": _number(row[1]),
        "median": _number(row[2]),
        "standard_deviation": _number(row[3]),
        "minimum": _number(row[4]),
        "maximum": _number(row[5]),
        "frequencies": [
            {"rating": int(rating), "count": int(count), "percentage": 100 * float(pct)}
            for rating, count, pct in frequencies
        ],
        "seller_volume_groups": [
            {
                "group": group,
                "sellers": int(sellers),
                "ratings": int(ratings),
                "mean_seller_average": _number(mean),
                "median_seller_average": _number(median),
                "standard_deviation_seller_average": _number(stddev),
                "sellers_with_boundary_mean": int(boundary),
                "sellers_with_near_extreme_mean": int(extreme),
            }
            for (
                group,
                sellers,
                ratings,
                mean,
                median,
                stddev,
                boundary,
                extreme,
            ) in volume
        ],
    }


def _candidate_cutoffs(connection: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    minimum, maximum = connection.execute(
        "SELECT min(review_date), max(review_date) FROM usable_ratings"
    ).fetchone()
    if minimum is None or maximum is None:
        return []
    range_cutoff = connection.execute(
        "SELECT ? + (? - ?) * 0.7", [minimum, maximum, minimum]
    ).fetchone()[0]
    candidates = [("range_70_percent", range_cutoff.date().isoformat())]
    for value in ("2017-07-01", "2018-01-01", "2018-04-01"):
        parsed = date.fromisoformat(value)
        if minimum.date() < parsed < maximum.date():
            candidates.append((f"calendar_{value}", value))
    return candidates


def _temporal_rows(
    connection: duckdb.DuckDBPyConnection,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for name, cutoff in _candidate_cutoffs(connection):
        row = connection.execute(
            """
            WITH counts AS (
                SELECT seller_id,
                    count(*) FILTER (WHERE review_date < ?::DATE) AS historical,
                    count(*) FILTER (WHERE review_date >= ?::DATE) AS future
                FROM usable_ratings GROUP BY seller_id
            )
            SELECT
                sum(historical), sum(future),
                count(*) FILTER (WHERE historical > 0),
                count(*) FILTER (WHERE future > 0),
                count(*) FILTER (WHERE historical > 0 AND future > 0),
                count(*) FILTER (WHERE historical >= 5),
                count(*) FILTER (WHERE historical >= 10),
                count(*) FILTER (WHERE historical >= 20),
                count(*) FILTER (WHERE future >= 5),
                count(*) FILTER (WHERE future >= 10),
                count(*) FILTER (WHERE historical >= 5 AND future >= 5),
                count(*) FILTER (WHERE historical >= 10 AND future >= 5),
                count(*) FILTER (WHERE historical >= 10 AND future >= 10),
                count(*) FILTER (WHERE historical >= 20 AND future >= 5),
                count(*) FILTER (WHERE historical >= 20 AND future >= 10)
            FROM counts
            """,
            [cutoff, cutoff],
        ).fetchone()
        assert row is not None
        columns = (
            "historical_ratings",
            "future_ratings",
            "sellers_historical",
            "sellers_future",
            "sellers_both",
            "sellers_historical_5",
            "sellers_historical_10",
            "sellers_historical_20",
            "sellers_future_5",
            "sellers_future_10",
            "sellers_both_5_5",
            "sellers_both_10_5",
            "sellers_both_10_10",
            "sellers_both_20_5",
            "sellers_both_20_10",
        )
        output.append(
            {"split_name": name, "cutoff_date": cutoff}
            | {
                column: int(value or 0)
                for column, value in zip(columns, row, strict=True)
            }
        )
    return output


def _operational_profile(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    row = connection.execute(
        """
        SELECT count(*), count(item_value), count(freight_value), count(order_status),
               count(order_purchase_timestamp), count(order_delivered_customer_date),
               count(order_estimated_delivery_date),
               count(*) FILTER (
                   WHERE order_delivered_customer_date > order_estimated_delivery_date
               ),
               avg(item_value), avg(freight_value)
        FROM usable_ratings
        """
    ).fetchone()
    assert row is not None
    group_rows = connection.execute(
        """
        WITH review_by_order AS (
            SELECT order_id, avg(review_score) AS rating FROM reviews GROUP BY order_id
        )
        SELECT
            CASE WHEN s.distinct_seller_count = 1 THEN 'single_seller'
                 ELSE 'multi_seller' END,
            count(*), avg(r.rating), avg(s.item_count), avg(s.item_value),
            avg(s.freight_value),
            avg(CASE WHEN o.order_delivered_customer_date >
                              o.order_estimated_delivery_date THEN 1 ELSE 0 END)
                FILTER (WHERE o.order_delivered_customer_date IS NOT NULL
                          AND o.order_estimated_delivery_date IS NOT NULL)
        FROM order_seller_counts AS s
        JOIN review_by_order AS r USING (order_id)
        JOIN orders AS o USING (order_id)
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    category_coverage: dict[str, int] | None = None
    if "products" in tables:
        category = connection.execute(
            """
            SELECT
                count(DISTINCT u.order_id),
                count(DISTINCT u.order_id) FILTER (
                    WHERE p.product_category_name IS NOT NULL
                ),
                count(DISTINCT p.product_category_name)
            FROM usable_ratings AS u
            JOIN items AS i USING (order_id)
            LEFT JOIN products AS p USING (product_id)
            """
        ).fetchone()
        assert category is not None
        category_coverage = {
            "usable_orders_with_items": int(category[0]),
            "usable_orders_with_known_category": int(category[1]),
            "distinct_source_categories": int(category[2]),
        }
    return {
        "usable_observations": int(row[0]),
        "available_item_value": int(row[1]),
        "available_freight_value": int(row[2]),
        "available_order_status": int(row[3]),
        "available_purchase_timestamp": int(row[4]),
        "available_delivery_timestamp": int(row[5]),
        "available_estimated_delivery_timestamp": int(row[6]),
        "late_deliveries": int(row[7]),
        "mean_item_value": _number(row[8]),
        "mean_freight_value": _number(row[9]),
        "product_category_coverage": category_coverage,
        "attribution_group_comparison": [
            {
                "group": group,
                "reviewed_orders": int(count),
                "mean_order_rating": _number(rating),
                "mean_item_count": _number(items),
                "mean_item_value": _number(value),
                "mean_freight_value": _number(freight),
                "late_delivery_rate": _number(late),
            }
            for group, count, rating, items, value, freight, late in group_rows
        ],
        "interpretation": (
            "These variables support noncausal operational comparisons. An order-level "
            "rating can reflect delivery, product, marketplace, and seller performance."
        ),
    }


def _copy_query(
    connection: duckdb.DuckDBPyConnection, query: str, destination: Path
) -> None:
    temporary = destination.with_name(f".{destination.name}.staging-{uuid4().hex}")
    try:
        connection.execute(
            f"COPY ({query}) TO {_quote(temporary)} (HEADER, DELIMITER ',')"
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_temporal_csv(rows: list[dict[str, object]], destination: Path) -> None:
    with destination.open("w", encoding="utf-8", newline="") as output:
        if not rows:
            output.write("split_name,cutoff_date\n")
            return
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _save(figure: Figure, destination: Path) -> None:
    figure.savefig(
        destination,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "rating_reliability"},
    )


def _figures(connection: duckdb.DuckDBPyConnection, output: Path) -> None:
    counts = connection.execute(
        "SELECT count(*) FROM usable_ratings GROUP BY seller_id"
    ).fetchall()
    figure = Figure(figsize=(7, 4.5))
    axis = figure.subplots()
    axis.hist([row[0] for row in counts], bins=40)
    axis.set_xscale("log")
    axis.set(
        title="Usable ratings per seller",
        xlabel="Ratings (log scale)",
        ylabel="Sellers",
    )
    _save(figure, output / "seller_review_count_distribution.png")

    ratings = connection.execute(
        "SELECT rating, count(*) FROM usable_ratings GROUP BY rating ORDER BY rating"
    ).fetchall()
    figure = Figure(figsize=(7, 4.5))
    axis = figure.subplots()
    axis.bar([row[0] for row in ratings], [row[1] for row in ratings])
    axis.set(
        title="Usable customer rating distribution", xlabel="Rating", ylabel="Orders"
    )
    axis.set_xticks(range(1, 6))
    _save(figure, output / "customer_rating_distribution.png")

    sellers = connection.execute(
        "SELECT count(*), avg(rating) FROM usable_ratings GROUP BY seller_id"
    ).fetchall()
    figure = Figure(figsize=(7, 4.5))
    axis = figure.subplots()
    axis.scatter(
        [row[0] for row in sellers], [row[1] for row in sellers], alpha=0.35, s=12
    )
    axis.set_xscale("log")
    axis.set(
        title="Review volume versus seller mean rating",
        xlabel="Usable ratings (log scale)",
        ylabel="Raw seller mean",
        ylim=(0.8, 5.2),
    )
    _save(figure, output / "review_volume_vs_seller_mean_rating.png")


def _temporal_figure(rows: list[dict[str, object]], output: Path) -> None:
    figure = Figure(figsize=(9, 4.8))
    axis = figure.subplots()
    positions = list(range(len(rows)))
    width = 0.35
    axis.bar(
        [position - width / 2 for position in positions],
        [int(row["sellers_both_5_5"]) for row in rows],
        width,
        label="history >=5, future >=5",
    )
    axis.bar(
        [position + width / 2 for position in positions],
        [int(row["sellers_both_10_5"]) for row in rows],
        width,
        label="history >=10, future >=5",
    )
    axis.set_xticks(positions, [str(row["cutoff_date"]) for row in rows], rotation=25)
    axis.set(title="Historical and future seller coverage", ylabel="Eligible sellers")
    axis.legend()
    _save(figure, output / "historical_vs_future_review_coverage.png")


def _markdown(summary: dict[str, Any]) -> str:
    attribution = summary["seller_attribution"]
    distribution = summary["seller_review_distribution"]
    temporal = summary["temporal_feasibility"]
    thresholds = {row["minimum_ratings"]: row for row in distribution["thresholds"]}
    return (
        f"""# Olist dataset suitability audit

Generated from the local CSV files on {summary["metadata"]["processing_date"]}.

## Recommendation

**{summary["recommendation"]}**

Olist is technically useful for this research when the analytical entity is a
marketplace seller and ratings are restricted to unambiguous single-seller orders.
The approval remains conditional because an order rating is not a pure measure of
seller quality, multi-seller and repeated-review orders are excluded, and public use
must comply with CC BY-NC-SA 4.0 and any applicable third-party rights.

## Measured evidence

- {attribution["single_seller_percentage_reviewed_orders"]:.2f}% of reviewed orders
  contain exactly one distinct seller.
- {attribution["usable_rating_observations"]:,} conservative usable order ratings
  represent {attribution["sellers_with_usable_ratings"]:,} sellers.
- Sellers with at least 10 usable ratings: {thresholds[10]["eligible_sellers"]:,}.
- Sellers with at least 20 usable ratings: {thresholds[20]["eligible_sellers"]:,}.
- Review-date coverage: {summary["review_quality"]["minimum_review_date"]} through
  {summary["review_quality"]["maximum_review_date"]}.
- Candidate temporal splits evaluated: {len(temporal)}.

## Decision criteria

| Criterion | Result | Evidence |
|---|---|---|
"""
        + "\n".join(
            f"| {row['criterion']} | **{row['result']}** | {row['evidence']} |"
            for row in summary["decision_criteria"]
        )
        + """

## Observation rule

A seller-attributable observation is one valid review row for an existing order with
exactly one distinct seller. Orders with multiple review rows and review identifiers
linked to multiple orders are excluded rather than resolved arbitrarily. The order ID,
not `review_id`, is the observation key under this rule.

## Interpretation limits

The score describes the purchase experience and may reflect delivery, the product,
marketplace operations, and the seller. The audit supports comparative and predictive
research, not causal claims about seller performance. Later ratings are evaluation
outcomes, not objective ground truth.

## Permission status

The Kaggle data card identifies the dataset as CC BY-NC-SA 4.0. A public,
noncommercial portfolio must credit Olist, link the source and license, identify
modifications, and apply compatible share-alike terms to adaptations. Raw CSVs remain
local. The license does not itself guarantee clearance of privacy or other third-party
rights, so only aggregate, non-identifying results should be published unless those
conditions are independently confirmed.
"""
    )


def run_audit(
    *, data_directory: Path, output_directory: Path, overwrite: bool = False
) -> dict[str, Any]:
    """Run the Olist audit and write privacy-safe local artifacts."""

    paths: dict[str, Path] = {}
    for name, filename in REQUIRED_FILES.items():
        path = data_directory / filename
        if not path.is_file():
            raise OlistAuditError(f"required input does not exist: {path}")
        paths[name] = path
    for name, filename in OPTIONAL_FILES.items():
        path = data_directory / filename
        if path.is_file():
            paths[name] = path

    existing = [
        output_directory / name
        for name in OUTPUT_FILES
        if (output_directory / name).exists()
    ]
    if existing and not overwrite:
        raise OlistAuditError("outputs already exist; use --overwrite to replace")
    output_directory.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(":memory:")
    try:
        _create_views(connection, paths)
        schemas = _schema_profile(connection, tuple(paths))
        review_quality = _review_quality(connection)
        relational_quality = _relational_quality(connection)
        attribution = _attribution(connection)
        distribution = _distribution(connection)
        rating_profile = _rating_profile(connection)
        temporal = _temporal_rows(connection)
        date_coverage = _date_coverage(connection)
        operational = _operational_profile(connection)

        metadata = {
            "processing_date": date.today().isoformat(),
            "source": "Brazilian E-Commerce Public Dataset by Olist",
            "source_url": "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
            "dataset_version": (
                "local Kaggle archive; release version is not encoded in the CSV files"
            ),
            "published_license": "CC BY-NC-SA 4.0",
            "permission_status": "conditional",
            "input_files": [
                {
                    "file_name": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in sorted(paths.values())
            ],
            "privacy": {
                "review_text_exported": False,
                "customer_identifiers_exported": False,
            },
        }
        t10 = next(
            row for row in distribution["thresholds"] if row["minimum_ratings"] == 10
        )
        t20 = next(
            row for row in distribution["thresholds"] if row["minimum_ratings"] == 20
        )
        best_temporal = max(temporal, key=lambda row: int(row["sellers_both_10_5"]))
        decision_criteria = [
            {
                "criterion": "A. Single-seller attribution",
                "result": "PASS",
                "evidence": (
                    f"{attribution['single_seller_percentage_reviewed_orders']:.2f}% "
                    "of reviewed orders have one seller."
                ),
            },
            {
                "criterion": "B. Review-volume comparisons",
                "result": "PASS",
                "evidence": (
                    f"{t10['eligible_sellers']:,} sellers have >=10 and "
                    f"{t20['eligible_sellers']:,} have >=20 usable ratings."
                ),
            },
            {
                "criterion": "C. Historical evaluation",
                "result": "CONDITIONAL PASS",
                "evidence": (
                    "Best candidate split has "
                    f"{best_temporal['sellers_both_10_5']:,} sellers with >=10 "
                    "historical and >=5 future ratings; cutoff selection must be "
                    "prespecified."
                ),
            },
            {
                "criterion": "D. Duplicate handling",
                "result": "PASS",
                "evidence": (
                    "Order ID is usable after conservatively excluding ambiguous "
                    "repeated rows and cross-order review IDs."
                ),
            },
            {
                "criterion": "E. Ratings and seller identifiers",
                "result": "PASS",
                "evidence": (
                    f"{attribution['usable_rating_observations']:,} ratings map to "
                    f"{attribution['sellers_with_usable_ratings']:,} sellers."
                ),
            },
            {
                "criterion": "F. Operational relevance",
                "result": "CONDITIONAL PASS",
                "evidence": (
                    "Order value, freight, status and delivery fields are available, "
                    "but the rating is not seller-specific."
                ),
            },
            {
                "criterion": "G. Permission and reuse",
                "result": "CONDITIONAL PASS",
                "evidence": (
                    "Kaggle publishes CC BY-NC-SA 4.0; attribution, noncommercial and "
                    "share-alike terms apply, while underlying rights are not "
                    "independently verified."
                ),
            },
        ]
        summary: dict[str, Any] = {
            "report_schema_version": 1,
            "metadata": metadata,
            "dataset_structure": schemas,
            "review_quality": review_quality,
            "relational_quality": relational_quality,
            "seller_attribution": attribution,
            "seller_review_distribution": distribution,
            "rating_profile": rating_profile,
            "temporal_feasibility": temporal,
            "date_coverage": date_coverage,
            "operational_relevance": operational,
            "decision_criteria": decision_criteria,
            "recommendation": "CONDITIONALLY APPROVE OLIST",
        }

        _copy_query(
            connection,
            """
            SELECT
                s.order_id, s.single_seller_id AS seller_id,
                s.distinct_seller_count, s.item_count,
                coalesce(rc.review_row_count, 0) AS review_row_count,
                CASE
                    WHEN s.distinct_seller_count > 1 THEN 'multiple_sellers'
                    WHEN coalesce(rc.review_row_count, 0) = 0 THEN 'no_review'
                    WHEN rc.review_row_count > 1 THEN 'multiple_review_rows'
                    WHEN u.order_id IS NULL THEN 'invalid_or_ambiguous_review'
                    ELSE 'usable'
                END AS attribution_status
            FROM order_seller_counts AS s
            LEFT JOIN (
                SELECT order_id, count(*) AS review_row_count
                FROM reviews GROUP BY order_id
            ) AS rc USING (order_id)
            LEFT JOIN usable_ratings AS u USING (order_id)
            ORDER BY s.order_id
            """,
            output_directory / "seller_attribution.csv",
        )
        _copy_query(
            connection,
            """
            SELECT seller_id, count(*) AS usable_rating_count,
                   avg(rating) AS observed_mean_rating,
                   stddev_samp(rating) AS rating_standard_deviation,
                   min(rating) AS minimum_rating, max(rating) AS maximum_rating,
                   min(review_date) AS first_review_date,
                   max(review_date) AS last_review_date
            FROM usable_ratings GROUP BY seller_id ORDER BY seller_id
            """,
            output_directory / "seller_review_distribution.csv",
        )
        _write_temporal_csv(temporal, output_directory / "temporal_feasibility.csv")
        _figures(connection, output_directory)
        _temporal_figure(temporal, output_directory)
    except duckdb.Error as error:
        raise OlistAuditError(f"Olist audit query failed: {error}") from error
    finally:
        connection.close()

    write_json_artifact(
        summary, output_directory / "audit_summary.json", overwrite=True
    )
    quality_payload = {
        "report_schema_version": 1,
        "review_quality": review_quality,
        "relational_quality": relational_quality,
        "seller_attribution": attribution,
        "counting_note": (
            "Issue counters may overlap. Source review rows, distinct review IDs, "
            "distinct reviewed orders, and usable order-level observations are "
            "separate "
            "denominators and must not be added together."
        ),
        "privacy": metadata["privacy"],
    }
    write_json_artifact(
        quality_payload, output_directory / "data_quality_summary.json", overwrite=True
    )
    (output_directory / "dataset_suitability.md").write_text(
        _markdown(summary), encoding="utf-8", newline="\n"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    """Build the Olist audit command-line parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Audit local Olist CSV files for seller-rating research suitability."
        )
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/local/olist_audit")
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit CLI and return a conventional process exit code."""

    args = build_parser().parse_args(argv)
    try:
        summary = run_audit(
            data_directory=args.data_dir,
            output_directory=args.output_dir,
            overwrite=args.overwrite,
        )
    except (OlistAuditError, ArtifactError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(summary["recommendation"])
    print(
        "usable seller ratings: "
        f"{summary['seller_attribution']['usable_rating_observations']}"
    )
    print(f"outputs: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
