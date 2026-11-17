"""Baseline metrics for business ratings."""

from __future__ import annotations

import math
from collections.abc import Iterable
from numbers import Real

MIN_RATING = 1.0
MAX_RATING = 5.0


def _validated_ratings(ratings: Iterable[Real]) -> tuple[float, ...]:
    """Materialize and validate a collection of ratings."""

    if isinstance(ratings, (str, bytes)):
        raise TypeError("ratings must be an iterable of numeric values")

    try:
        values = tuple(ratings)
    except TypeError as error:
        raise TypeError("ratings must be an iterable of numeric values") from error

    if not values:
        raise ValueError("ratings must contain at least one value")

    validated: list[float] = []
    for rating in values:
        if isinstance(rating, bool) or not isinstance(rating, Real):
            raise TypeError("each rating must be a real numeric value")
        numeric_rating = float(rating)
        if not math.isfinite(numeric_rating):
            raise ValueError("ratings must contain only finite values")
        if not MIN_RATING <= numeric_rating <= MAX_RATING:
            raise ValueError("ratings must be between 1 and 5 inclusive")
        validated.append(numeric_rating)

    return tuple(validated)


def _validated_real_argument(value: Real, *, name: str) -> float:
    """Validate and convert one real-valued function argument."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real numeric value")
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{name} must be finite")
    return numeric_value


def arithmetic_mean_rating(ratings: Iterable[Real]) -> float:
    """Return the arithmetic mean of ratings on the inclusive 1--5 scale.

    Args:
        ratings: A non-empty iterable of finite, real numeric ratings.

    Raises:
        TypeError: If the collection or any rating is not numeric.
        ValueError: If the collection is empty or contains a nonfinite or
            out-of-range value.
    """

    values = _validated_ratings(ratings)
    return math.fsum(values) / len(values)


def adjusted_mean_rating(
    ratings: Iterable[Real], reference_mean: Real, prior_strength: Real
) -> float:
    """Return a reference-mean shrinkage estimate for a business's rating.

    The estimate is ``(n * sample_mean + m * reference_mean) / (n + m)``, where
    ``n`` is the observed review count and ``m`` is ``prior_strength``. It pulls
    small samples more strongly toward the reference mean. This adjusted score
    is a shrinkage estimator, not an uncertainty interval or confidence bound.

    Args:
        ratings: A non-empty iterable of finite ratings in the range 1--5.
        reference_mean: A finite reference mean in the inclusive range 1--5.
        prior_strength: A finite, strictly positive weight for the reference.

    Raises:
        TypeError: If an input expected to be numeric is not a real number.
        ValueError: If ratings are empty or any argument violates its range.
    """

    reference = _validated_real_argument(reference_mean, name="reference_mean")
    if not MIN_RATING <= reference <= MAX_RATING:
        raise ValueError("reference_mean must be between 1 and 5 inclusive")

    strength = _validated_real_argument(prior_strength, name="prior_strength")
    if strength <= 0:
        raise ValueError("prior_strength must be positive")

    values = _validated_ratings(ratings)
    sample_total = math.fsum(values)
    return (sample_total + strength * reference) / (len(values) + strength)
