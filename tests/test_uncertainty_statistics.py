"""Synthetic tests for Dirichlet and posterior predictive calculations."""

import numpy as np
import pytest

from rating_reliability.uncertainty import (
    credible_intervals,
    posterior_alpha,
    posterior_expected_rating,
    predictive_intervals,
    prior_alpha,
)


def test_posterior_mean_equals_locked_shrinkage_formula() -> None:
    probabilities = np.asarray([0.1, 0.2, 0.2, 0.3, 0.2])
    counts = np.asarray([[1, 0, 2, 1, 1], [0, 0, 0, 0, 1]])
    strength = 10.0
    alpha = posterior_alpha(counts, probabilities, strength)
    posterior_mean = posterior_expected_rating(alpha)
    stars = np.arange(1, 6)
    reference = float(probabilities @ stars)
    expected = (counts @ stars + strength * reference) / (counts.sum(axis=1) + strength)

    assert posterior_mean == pytest.approx(expected)


def test_zero_category_policy_uses_valid_lower_dimensional_support() -> None:
    probabilities = np.asarray([0.5, 0.0, 0.0, 0.0, 0.5])
    alpha = posterior_alpha(np.asarray([[1, 0, 0, 0, 1]]), probabilities, 2)
    first = credible_intervals(alpha, draws=500, interval_level=0.95, seed=42)
    second = credible_intervals(alpha, draws=500, interval_level=0.95, seed=42)

    assert prior_alpha(probabilities, 2).tolist() == [1, 0, 0, 0, 1]
    assert first.means[0] == pytest.approx(3)
    assert first.lower[0] >= 1
    assert first.upper[0] <= 5
    assert first.lower.tolist() == second.lower.tolist()
    assert first.upper.tolist() == second.upper.tolist()


def test_predictive_intervals_condition_on_sample_size_and_are_reproducible() -> None:
    probabilities = np.asarray([0.1, 0.1, 0.2, 0.3, 0.3])
    alpha = posterior_alpha(
        np.asarray([[0, 0, 0, 2, 3], [0, 0, 0, 2, 3]]), probabilities, 5
    )
    sizes = np.asarray([1, 20])
    outcomes = np.asarray([5.0, 4.25])
    first = predictive_intervals(
        alpha,
        sizes,
        outcomes,
        draws=1_000,
        interval_level=0.95,
        seed=7,
    )
    second = predictive_intervals(
        alpha,
        sizes,
        outcomes,
        draws=1_000,
        interval_level=0.95,
        seed=7,
    )

    assert np.all((first.lower >= 1) & (first.upper <= 5))
    assert first.upper[1] - first.lower[1] < first.upper[0] - first.lower[0]
    assert first.lower.tolist() == second.lower.tolist()
    assert first.covered.tolist() == second.covered.tolist()
