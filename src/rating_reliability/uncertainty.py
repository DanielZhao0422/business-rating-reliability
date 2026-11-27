"""Empirical-Bayes Dirichlet uncertainty and predictive simulation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


class UncertaintyError(ValueError):
    """Raised when Dirichlet inputs or simulated outputs are invalid."""


@dataclass(frozen=True)
class IntervalResults:
    """Posterior expected-rating interval results."""

    means: NDArray[np.float64]
    lower: NDArray[np.float64]
    upper: NDArray[np.float64]


@dataclass(frozen=True)
class PredictiveIntervalResults:
    """Future-sample-mean predictive intervals and coverage."""

    lower: NDArray[np.float64]
    upper: NDArray[np.float64]
    covered: NDArray[np.bool_]


def prior_alpha(
    reference_probabilities: NDArray[np.float64], prior_strength: float
) -> NDArray[np.float64]:
    """Return concentration parameters, preserving zero-probability support."""

    probabilities = np.asarray(reference_probabilities, dtype=np.float64)
    if probabilities.shape != (5,):
        raise UncertaintyError("reference probabilities must have five categories")
    if np.any(~np.isfinite(probabilities)) or np.any(probabilities < 0):
        raise UncertaintyError("reference probabilities must be finite and nonnegative")
    if not np.isclose(probabilities.sum(), 1.0, rtol=0, atol=1e-12):
        raise UncertaintyError("reference probabilities must sum to one")
    if not np.isfinite(prior_strength) or prior_strength <= 0:
        raise UncertaintyError("prior strength must be positive and finite")
    return probabilities * prior_strength


def posterior_alpha(
    category_counts: NDArray[np.int64],
    reference_probabilities: NDArray[np.float64],
    prior_strength: float,
) -> NDArray[np.float64]:
    """Add seller category counts to pooled prior concentration."""

    counts = np.asarray(category_counts)
    if counts.ndim not in (1, 2) or counts.shape[-1] != 5:
        raise UncertaintyError("category counts must end with five categories")
    if np.any(counts < 0) or np.any(counts != np.floor(counts)):
        raise UncertaintyError("category counts must be nonnegative integers")
    return counts.astype(np.float64) + prior_alpha(
        reference_probabilities, prior_strength
    )


def posterior_expected_rating(alpha: NDArray[np.float64]) -> NDArray[np.float64]:
    """Calculate posterior expected star rating from concentration parameters."""

    values = np.asarray(alpha, dtype=np.float64)
    if values.ndim not in (1, 2) or values.shape[-1] != 5:
        raise UncertaintyError("posterior alpha must end with five categories")
    if np.any(values < 0) or np.any(values.sum(axis=-1) <= 0):
        raise UncertaintyError("posterior alpha must have positive total mass")
    return (values @ np.arange(1, 6, dtype=np.float64)) / values.sum(axis=-1)


def _dirichlet_draws(
    alpha: NDArray[np.float64], draws: int, rng: np.random.Generator
) -> NDArray[np.float64]:
    """Draw on active support without passing zero alpha to NumPy.

    A pooled zero-probability category has no observations anywhere in that
    training window. The model is therefore a lower-dimensional Dirichlet on
    the positive-probability support, with exact zeros restored afterward.
    """

    values = np.asarray(alpha, dtype=np.float64)
    if values.shape != (5,) or np.any(values < 0) or values.sum() <= 0:
        raise UncertaintyError(
            "alpha must have five nonnegative values and positive mass"
        )
    if draws <= 0:
        raise UncertaintyError("draw count must be positive")
    active = values > 0
    result = np.zeros((draws, 5), dtype=np.float64)
    if int(active.sum()) == 1:
        result[:, active] = 1.0
    else:
        result[:, active] = rng.dirichlet(values[active], size=draws)
    return result


def credible_intervals(
    alphas: NDArray[np.float64],
    *,
    draws: int,
    interval_level: float,
    seed: int,
) -> IntervalResults:
    """Calculate equal-tailed posterior intervals for expected star rating."""

    values = np.asarray(alphas, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5:
        raise UncertaintyError("alphas must have shape (sellers, 5)")
    if not 0 < interval_level < 1:
        raise UncertaintyError("interval level must be between zero and one")
    means = np.asarray(posterior_expected_rating(values), dtype=np.float64)
    lower = np.empty(len(values), dtype=np.float64)
    upper = np.empty(len(values), dtype=np.float64)
    tail = (1 - interval_level) / 2
    stars = np.arange(1, 6, dtype=np.float64)
    rng = np.random.default_rng(seed)
    for index, alpha in enumerate(values):
        expected_draws = _dirichlet_draws(alpha, draws, rng) @ stars
        lower[index], upper[index] = np.quantile(expected_draws, [tail, 1 - tail])
    if np.any(lower < 1 - 1e-12) or np.any(upper > 5 + 1e-12):
        raise UncertaintyError("credible interval left the rating scale")
    return IntervalResults(means, lower, upper)


def predictive_intervals(
    alphas: NDArray[np.float64],
    future_sample_sizes: NDArray[np.int64],
    observed_future_means: NDArray[np.float64],
    *,
    draws: int,
    interval_level: float,
    seed: int,
) -> PredictiveIntervalResults:
    """Simulate future sample means conditional on realized future sample size."""

    values = np.asarray(alphas, dtype=np.float64)
    sizes = np.asarray(future_sample_sizes, dtype=np.int64)
    observed = np.asarray(observed_future_means, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 5:
        raise UncertaintyError("alphas must have shape (sellers, 5)")
    if not (len(values) == len(sizes) == len(observed)):
        raise UncertaintyError("predictive inputs must have equal lengths")
    if np.any(sizes <= 0):
        raise UncertaintyError("future sample sizes must be positive")
    if not 0 < interval_level < 1:
        raise UncertaintyError("interval level must be between zero and one")
    lower = np.empty(len(values), dtype=np.float64)
    upper = np.empty(len(values), dtype=np.float64)
    tail = (1 - interval_level) / 2
    stars = np.arange(1, 6, dtype=np.float64)
    rng = np.random.default_rng(seed)
    for index, (alpha, size) in enumerate(zip(values, sizes, strict=True)):
        probabilities = _dirichlet_draws(alpha, draws, rng)
        future_counts = rng.multinomial(int(size), probabilities)
        future_means = (future_counts @ stars) / size
        lower[index], upper[index] = np.quantile(future_means, [tail, 1 - tail])
    covered = (observed >= lower - 1e-12) & (observed <= upper + 1e-12)
    if np.any(lower < 1 - 1e-12) or np.any(upper > 5 + 1e-12):
        raise UncertaintyError("predictive interval left the rating scale")
    return PredictiveIntervalResults(lower, upper, covered)


def summarize_intervals(
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
    covered: NDArray[np.bool_] | None = None,
) -> dict[str, float]:
    """Summarize interval width and optional empirical coverage."""

    widths = np.asarray(upper) - np.asarray(lower)
    result = {
        "mean_width": float(widths.mean()),
        "median_width": float(np.median(widths)),
    }
    if covered is not None:
        result["empirical_coverage"] = float(np.asarray(covered).mean())
    return result
