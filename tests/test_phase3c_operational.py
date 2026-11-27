"""Synthetic tests for Phase 3C operational definitions."""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
import pytest

from rating_reliability.operational import (
    OperationalCohort,
    operational_population_summary,
    seller_delivery_aggregates,
)


@pytest.fixture
def delivery_cohort() -> OperationalCohort:
    return OperationalCohort(
        order_ids=np.asarray(["o1", "o2", "o3", "o4"]),
        seller_ids=np.asarray(["a", "a", "a", "b"]),
        statuses=np.asarray(["delivered", "delivered", "canceled", "delivered"]),
        delivery_delays=np.asarray([2.0, -1.0, np.nan, 3.0]),
        delivered_late=np.asarray([1.0, 0.0, np.nan, np.nan]),
        item_values=np.asarray([10.0, 20.0, 30.0, 40.0]),
        freight_values=np.asarray([1.0, 2.0, 3.0, 4.0]),
        historical_counts=np.asarray([2, 2, 2, 5]),
        raw_estimates=np.asarray([4.0, 4.0, 4.0, 3.0]),
        adjusted_estimates=np.asarray([3.8, 3.8, 3.8, 3.2]),
    )


def test_seller_delivery_aggregation_excludes_unresolved_and_cancelled(
    delivery_cohort: OperationalCohort,
) -> None:
    result = seller_delivery_aggregates(delivery_cohort)

    assert result["seller_ids"].tolist() == ["a"]
    assert result["evaluable_orders"].tolist() == [2]
    assert result["late_orders"].tolist() == [1]
    assert result["late_rate"].tolist() == [0.5]
    assert result["mean_delay_days"].tolist() == [0.5]


def test_population_summary_uses_the_current_study_counts(
    delivery_cohort: OperationalCohort,
) -> None:
    with duckdb.connect() as connection:
        connection.execute(
            """
            CREATE TABLE source_orders AS SELECT 'o1' AS order_id;
            CREATE TABLE seller_rating_observations AS SELECT 'o1' AS order_id;
            CREATE TABLE order_product_categories (
                order_id VARCHAR, product_category VARCHAR
            );
            CREATE TABLE order_eligibility AS SELECT 'ELIGIBLE' AS primary_reason;
            """
        )
        result = operational_population_summary(
            connection,
            delivery_cohort,
            cutoff=date(2018, 2, 3),
            historical_sellers=5,
            evaluation_sellers=7,
            paired_sellers=2,
        )

    assert result["selection"] == {
        "source_orders": 1,
        "eligible_seller_rating_orders": 1,
        "eligible_proportion": 1.0,
        "primary_exclusion_reasons": {"ELIGIBLE": 1},
        "historical_sellers": 5,
        "evaluation_sellers": 7,
        "both_period_sellers": 2,
        "historical_only_sellers": 3,
        "evaluation_only_sellers": 5,
    }
    assert result["future_operational_cohort"]["rating_availability_rule"] == (
        "review_answer_timestamp >= 2018-02-03"
    )
    assert result["future_operational_cohort"]["category_missing_orders"] == 4
