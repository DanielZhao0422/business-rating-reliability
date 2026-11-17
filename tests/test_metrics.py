"""Tests for baseline metrics using synthetic ratings only."""

import math

import pytest

from rating_reliability.metrics import adjusted_mean_rating, arithmetic_mean_rating


def test_arithmetic_mean_rating_standard_example() -> None:
    assert arithmetic_mean_rating([2, 3, 4, 5]) == pytest.approx(3.5)


def test_adjusted_mean_shrinks_low_review_count_toward_reference() -> None:
    observed_rating = 5.0
    adjusted = adjusted_mean_rating(
        [observed_rating], reference_mean=3.5, prior_strength=9
    )

    assert adjusted == pytest.approx(3.65)
    assert 3.5 < adjusted < observed_rating


def test_adjusted_mean_large_sample_stays_close_to_observed_mean() -> None:
    ratings = [4.0] * 1_000
    adjusted = adjusted_mean_rating(ratings, reference_mean=3.0, prior_strength=10)

    assert adjusted == pytest.approx((1_000 * 4.0 + 10 * 3.0) / 1_010)
    assert abs(adjusted - 4.0) < abs(adjusted - 3.0)


@pytest.mark.parametrize("function", [arithmetic_mean_rating, adjusted_mean_rating])
def test_empty_ratings_are_rejected(function: object) -> None:
    if function is adjusted_mean_rating:
        with pytest.raises(ValueError, match="at least one"):
            adjusted_mean_rating([], reference_mean=3.5, prior_strength=5)
    else:
        with pytest.raises(ValueError, match="at least one"):
            arithmetic_mean_rating([])


@pytest.mark.parametrize(
    ("ratings", "expected_exception"),
    [
        ([0], ValueError),
        ([6], ValueError),
        ([math.nan], ValueError),
        ([math.inf], ValueError),
        (["5"], TypeError),
        ([True], TypeError),
    ],
)
def test_invalid_ratings_are_rejected(
    ratings: list[object], expected_exception: type[Exception]
) -> None:
    with pytest.raises(expected_exception):
        arithmetic_mean_rating(ratings)  # type: ignore[arg-type]


@pytest.mark.parametrize("reference_mean", [0, 6, math.nan, math.inf])
def test_invalid_reference_mean_is_rejected(reference_mean: float) -> None:
    with pytest.raises(ValueError):
        adjusted_mean_rating([4], reference_mean, prior_strength=5)


@pytest.mark.parametrize("prior_strength", [0, -1, math.nan, math.inf])
def test_invalid_prior_strength_is_rejected(prior_strength: float) -> None:
    with pytest.raises(ValueError):
        adjusted_mean_rating([4], reference_mean=3.5, prior_strength=prior_strength)


@pytest.mark.parametrize(
    ("reference_mean", "prior_strength"), [("3.5", 5), (3.5, "5"), (True, 5)]
)
def test_nonnumeric_prior_parameters_are_rejected(
    reference_mean: object, prior_strength: object
) -> None:
    with pytest.raises(TypeError):
        adjusted_mean_rating(
            [4],
            reference_mean=reference_mean,  # type: ignore[arg-type]
            prior_strength=prior_strength,  # type: ignore[arg-type]
        )
