"""Synthetic tests for predictive metrics, ranks, and paired inference."""

import numpy as np
import pytest

from rating_reliability.evaluation import (
    EvaluationData,
    error_metrics,
    paired_mae_bootstrap,
)
from rating_reliability.ranking import (
    average_ranks,
    kendall_tau_b,
    posterior_ranking_stability,
    ranking_comparison,
    spearman_correlation,
)


def _evaluation_data() -> EvaluationData:
    return EvaluationData(
        seller_ids=np.asarray(["a", "b", "c"]),
        historical_counts=np.asarray([1, 5, 20]),
        historical_category_counts=np.asarray(
            [[0, 0, 0, 0, 1], [0, 0, 1, 2, 2], [1, 1, 2, 6, 10]]
        ),
        raw_estimates=np.asarray([5.0, 4.2, 4.15]),
        adjusted_estimates=np.asarray([4.2, 4.1, 4.12]),
        evaluation_counts=np.asarray([1, 2, 4]),
        evaluation_means=np.asarray([4.0, 4.0, 4.25]),
    )


def test_error_metrics_use_seller_and_review_weighting_as_specified() -> None:
    estimates = np.asarray([1.0, 5.0])
    outcomes = np.asarray([2.0, 3.0])
    counts = np.asarray([1, 3])
    result = error_metrics(estimates, outcomes, counts)

    assert result.seller_equal_mae == pytest.approx(1.5)
    assert result.seller_equal_rmse == pytest.approx(np.sqrt(2.5))
    assert result.review_weighted_mae == pytest.approx(1.75)
    assert result.review_weighted_rmse == pytest.approx(np.sqrt(3.25))


def test_paired_bootstrap_is_reproducible_and_resamples_sellers() -> None:
    data = _evaluation_data()
    first = paired_mae_bootstrap(data, resamples=500, interval_level=0.95, seed=42)
    second = paired_mae_bootstrap(data, resamples=500, interval_level=0.95, seed=42)

    assert first == second
    assert first["unit"] == "seller"
    assert first["preserve_pairing"] is True
    assert first["seller_count"] == 3


def test_rank_metrics_use_average_ties_and_fixed_universe() -> None:
    raw = np.asarray([5.0, 4.0, 4.0, 3.0])
    adjusted = np.asarray([5.0, 3.0, 4.0, 4.0])
    sellers = np.asarray(["a", "b", "c", "d"])
    result = ranking_comparison(raw, adjusted, sellers, (1, 2))

    assert average_ranks(raw).tolist() == [1.0, 2.5, 2.5, 4.0]
    assert result["seller_count"] == 4
    assert result["tie_method"] == "average_rank"
    assert 0 <= result["top_k"]["2"]["overlap_proportion"] <= 1
    assert spearman_correlation(np.asarray([3, 2, 1]), np.asarray([3, 1, 2])) == (
        pytest.approx(0.5)
    )
    assert kendall_tau_b(np.asarray([3, 2, 1]), np.asarray([3, 1, 2])) == (
        pytest.approx(1 / 3)
    )


def test_posterior_rank_stability_is_batched_and_reproducible() -> None:
    alphas = np.asarray([[1.0, 1.0, 1.0, 3.0, 5.0], [2.0, 2.0, 2.0, 2.0, 2.0]])
    sellers = np.asarray(["a", "b"])
    first = posterior_ranking_stability(
        alphas, sellers, draws=200, top_k_values=(1,), seed=42, batch_size=25
    )
    second = posterior_ranking_stability(
        alphas, sellers, draws=200, top_k_values=(1,), seed=42, batch_size=25
    )

    assert first == second
    assert first["seller_count"] == 2
    assert first["draws"] == 200
    assert first["top_k_probability_summary"]["1"][
        "mean_top_k_probability"
    ] == pytest.approx(0.5)
