"""Leakage-safe SQL extraction and seller rating aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
import numpy as np
from numpy.typing import NDArray


class StatisticalDataError(ValueError):
    """Raised when analytical data violate a statistical invariant."""


@dataclass(frozen=True)
class SellerAggregates:
    """One row per seller for one explicitly bounded rating window."""

    seller_ids: NDArray[np.str_]
    review_counts: NDArray[np.int64]
    rating_sums: NDArray[np.float64]
    mean_ratings: NDArray[np.float64]
    category_counts: NDArray[np.int64]

    def __len__(self) -> int:
        return len(self.seller_ids)


@dataclass(frozen=True)
class ReferenceDistribution:
    """Pooled rating-category distribution for a training window."""

    category_counts: NDArray[np.int64]
    probabilities: NDArray[np.float64]
    mean_rating: float
    observation_count: int


def _where_window(start: date | None, end: date) -> tuple[str, list[date]]:
    conditions = ["review_answer_timestamp < ?"]
    parameters = [end]
    if start is not None:
        conditions.insert(0, "review_answer_timestamp >= ?")
        parameters.insert(0, start)
    return " AND ".join(conditions), parameters


def aggregate_sellers(
    connection: duckdb.DuckDBPyConnection,
    *,
    end_exclusive: date,
    start_inclusive: date | None = None,
) -> SellerAggregates:
    """Aggregate seller ratings in ``[start, end)`` with deterministic ordering."""

    where, parameters = _where_window(start_inclusive, end_exclusive)
    rows = connection.execute(
        f"""
        SELECT
            seller_id,
            count(*)::BIGINT AS review_count,
            sum(rating)::DOUBLE AS rating_sum,
            avg(rating)::DOUBLE AS mean_rating,
            count(*) FILTER (WHERE rating = 1)::BIGINT AS count_1,
            count(*) FILTER (WHERE rating = 2)::BIGINT AS count_2,
            count(*) FILTER (WHERE rating = 3)::BIGINT AS count_3,
            count(*) FILTER (WHERE rating = 4)::BIGINT AS count_4,
            count(*) FILTER (WHERE rating = 5)::BIGINT AS count_5
        FROM seller_rating_observations
        WHERE {where}
        GROUP BY seller_id
        ORDER BY seller_id
        """,
        parameters,
    ).fetchall()
    seller_ids = np.asarray([str(row[0]) for row in rows], dtype=np.str_)
    review_counts = np.asarray([row[1] for row in rows], dtype=np.int64)
    rating_sums = np.asarray([row[2] for row in rows], dtype=np.float64)
    mean_ratings = np.asarray([row[3] for row in rows], dtype=np.float64)
    if rows:
        category_counts = np.asarray([row[4:9] for row in rows], dtype=np.int64)
    else:
        category_counts = np.empty((0, 5), dtype=np.int64)
    result = SellerAggregates(
        seller_ids=seller_ids,
        review_counts=review_counts,
        rating_sums=rating_sums,
        mean_ratings=mean_ratings,
        category_counts=category_counts,
    )
    validate_seller_aggregates(result)
    return result


def validate_seller_aggregates(aggregates: SellerAggregates) -> None:
    """Verify category, sum, range, uniqueness, and shape invariants."""

    size = len(aggregates)
    if any(
        len(array) != size
        for array in (
            aggregates.review_counts,
            aggregates.rating_sums,
            aggregates.mean_ratings,
            aggregates.category_counts,
        )
    ) or aggregates.category_counts.shape != (size, 5):
        raise StatisticalDataError("seller aggregate arrays have inconsistent shapes")
    if size == 0:
        return
    if len(np.unique(aggregates.seller_ids)) != size:
        raise StatisticalDataError("seller aggregates contain duplicate seller IDs")
    if np.any(aggregates.review_counts <= 0):
        raise StatisticalDataError("seller review counts must be positive")
    if np.any(aggregates.category_counts < 0):
        raise StatisticalDataError("rating category counts cannot be negative")
    if not np.array_equal(
        aggregates.category_counts.sum(axis=1), aggregates.review_counts
    ):
        raise StatisticalDataError("category counts do not sum to seller review counts")
    stars = np.arange(1, 6, dtype=np.float64)
    reconstructed = aggregates.category_counts @ stars
    if not np.allclose(reconstructed, aggregates.rating_sums, rtol=0, atol=1e-12):
        raise StatisticalDataError("category counts do not reproduce rating sums")
    expected_means = aggregates.rating_sums / aggregates.review_counts
    if not np.allclose(expected_means, aggregates.mean_ratings, rtol=0, atol=1e-12):
        raise StatisticalDataError("seller means do not equal sums divided by counts")
    if np.any((aggregates.mean_ratings < 1) | (aggregates.mean_ratings > 5)):
        raise StatisticalDataError("seller means must remain within the rating scale")


def pooled_reference(
    connection: duckdb.DuckDBPyConnection,
    *,
    end_exclusive: date,
    start_inclusive: date | None = None,
) -> ReferenceDistribution:
    """Estimate a pooled category distribution from one training window only."""

    where, parameters = _where_window(start_inclusive, end_exclusive)
    rows = connection.execute(
        f"""
        SELECT rating, count(*)::BIGINT
        FROM seller_rating_observations
        WHERE {where}
        GROUP BY rating
        ORDER BY rating
        """,
        parameters,
    ).fetchall()
    counts = np.zeros(5, dtype=np.int64)
    for rating, count in rows:
        if not 1 <= int(rating) <= 5:
            raise StatisticalDataError("reference data contain an invalid rating")
        counts[int(rating) - 1] = int(count)
    total = int(counts.sum())
    if total <= 0:
        raise StatisticalDataError("reference training window contains no ratings")
    probabilities = counts.astype(np.float64) / total
    if not np.isclose(probabilities.sum(), 1.0, rtol=0, atol=1e-12):
        raise StatisticalDataError("reference probabilities do not sum to one")
    mean = float(probabilities @ np.arange(1, 6, dtype=np.float64))
    if not 1 <= mean <= 5:
        raise StatisticalDataError("reference mean is outside the rating scale")
    return ReferenceDistribution(counts, probabilities, mean, total)


def assert_window_integrity(
    connection: duckdb.DuckDBPyConnection,
    *,
    cutoff: date,
) -> dict[str, object]:
    """Assert the final temporal split and return independently queried counts."""

    row = connection.execute(
        """
        SELECT
            count(*)::BIGINT,
            count(*) FILTER (WHERE review_answer_timestamp < ?)::BIGINT,
            count(*) FILTER (WHERE review_answer_timestamp >= ?)::BIGINT,
            max(review_answer_timestamp) FILTER (WHERE review_answer_timestamp < ?),
            min(review_answer_timestamp) FILTER (WHERE review_answer_timestamp >= ?)
        FROM seller_rating_observations
        """,
        [cutoff, cutoff, cutoff, cutoff],
    ).fetchone()
    if row is None or int(row[0]) != int(row[1]) + int(row[2]):
        raise StatisticalDataError("historical/evaluation split does not reconcile")
    if row[3] is not None and row[3].date() >= cutoff:
        raise StatisticalDataError("historical data cross the final cutoff")
    if row[4] is not None and row[4].date() < cutoff:
        raise StatisticalDataError("evaluation data precede the final cutoff")
    return {
        "total_ratings": int(row[0]),
        "historical_ratings": int(row[1]),
        "evaluation_ratings": int(row[2]),
        "maximum_historical_date": row[3].isoformat() if row[3] else None,
        "minimum_evaluation_date": row[4].isoformat() if row[4] else None,
    }
