"""Synthetic tests for historical extraction and prior tuning."""

from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pytest
from tests.statistical_helpers import create_statistical_database, write_fast_protocol

from rating_reliability.evaluation import adjusted_estimates, tune_prior_strength
from rating_reliability.historical import (
    aggregate_sellers,
    assert_window_integrity,
    pooled_reference,
)
from rating_reliability.statistical_analysis import date_after_data
from rating_reliability.statistical_config import load_statistical_protocol

PROJECT_ROOT = Path(__file__).parents[1]


def test_historical_aggregation_and_reference_exclude_future(tmp_path: Path) -> None:
    database = create_statistical_database(tmp_path / "stats.duckdb")
    with duckdb.connect(str(database), read_only=True) as connection:
        historical = aggregate_sellers(connection, end_exclusive=date(2018, 1, 1))
        reference = pooled_reference(connection, end_exclusive=date(2018, 1, 1))
        split = assert_window_integrity(connection, cutoff=date(2018, 1, 1))
        end = date_after_data(connection)

    assert historical.seller_ids.tolist() == ["s1", "s2"]
    assert historical.review_counts.tolist() == [4, 4]
    assert historical.rating_sums.tolist() == [15.0, 11.0]
    assert historical.mean_ratings.tolist() == [3.75, 2.75]
    assert historical.category_counts.tolist() == [[1, 0, 0, 1, 2], [0, 2, 1, 1, 0]]
    assert reference.observation_count == 8
    assert reference.category_counts.tolist() == [1, 2, 1, 2, 2]
    assert reference.probabilities.sum() == pytest.approx(1)
    assert reference.mean_rating == pytest.approx(3.25)
    assert split["historical_ratings"] == 8
    assert split["evaluation_ratings"] == 4
    assert end == date(2018, 2, 12)


def test_shrinkage_vector_obeys_bounds_and_large_sample_limit() -> None:
    from rating_reliability.historical import SellerAggregates

    aggregate = SellerAggregates(
        seller_ids=np.asarray(["small", "large"]),
        review_counts=np.asarray([1, 10_000]),
        rating_sums=np.asarray([5.0, 50_000.0]),
        mean_ratings=np.asarray([5.0, 5.0]),
        category_counts=np.asarray([[0, 0, 0, 0, 1], [0, 0, 0, 0, 10_000]]),
    )
    result = adjusted_estimates(aggregate, 3.0, 10)

    assert 3 < result[0] < 5
    assert abs(result[1] - 5) < abs(result[0] - 5)
    assert adjusted_estimates(aggregate, 5.0, 10).tolist() == [5.0, 5.0]
    with pytest.raises(ValueError, match="positive"):
        adjusted_estimates(aggregate, 3.0, 0)


def test_tuning_uses_locked_candidates_and_is_unchanged_by_final_outcomes(
    tmp_path: Path,
) -> None:
    database = create_statistical_database(tmp_path / "stats.duckdb")
    config = write_fast_protocol(
        PROJECT_ROOT / "config" / "statistical_protocol.toml",
        tmp_path / "protocol.toml",
    )
    protocol = load_statistical_protocol(config)
    with duckdb.connect(str(database)) as connection:
        before = tune_prior_strength(connection, protocol)
        connection.execute(
            "UPDATE seller_rating_observations SET rating = 1 "
            "WHERE review_answer_timestamp >= DATE '2018-01-01'"
        )
        after = tune_prior_strength(connection, protocol)

    assert before == after
    assert before["candidate_strengths"] == [1, 2, 5, 10, 20, 50, 100]
    assert before["fallback_used"] is False
    assert before["final_evaluation_accessed"] is False
    assert len(before["folds"]) == 2
    assert [fold["training_ratings"] for fold in before["folds"]] == [4, 6]
    assert [fold["validation_ratings"] for fold in before["folds"]] == [2, 2]


def test_tuning_fallback_uses_locked_value_when_coverage_is_inadequate(
    tmp_path: Path,
) -> None:
    database = create_statistical_database(tmp_path / "stats.duckdb")
    protocol = load_statistical_protocol(
        PROJECT_ROOT / "config" / "statistical_protocol.toml"
    )
    with duckdb.connect(str(database), read_only=True) as connection:
        result = tune_prior_strength(connection, protocol)

    assert result["fallback_used"] is True
    assert result["selected_prior_strength"] == 10
    assert result["fallback_reason"]


def test_tuning_ties_select_smallest_candidate(tmp_path: Path) -> None:
    database = create_statistical_database(tmp_path / "stats.duckdb")
    config = write_fast_protocol(
        PROJECT_ROOT / "config" / "statistical_protocol.toml",
        tmp_path / "protocol.toml",
    )
    protocol = load_statistical_protocol(config)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE seller_rating_observations SET rating = 3 "
            "WHERE review_answer_timestamp < DATE '2018-01-01'"
        )
        result = tune_prior_strength(connection, protocol)

    assert result["fallback_used"] is False
    assert result["selected_prior_strength"] == 1
    assert result["tie_candidates"] == [1, 2, 5, 10, 20, 50, 100]
