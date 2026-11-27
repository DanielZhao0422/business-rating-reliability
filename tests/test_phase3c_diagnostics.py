"""Synthetic tests for Phase 3C sensitivity and calibration helpers."""

from __future__ import annotations

import numpy as np
import pytest

from rating_reliability.calibration import miss_classification
from rating_reliability.historical import SellerAggregates
from rating_reliability.sensitivity import seller_equal_reference


def test_seller_equal_reference_does_not_review_weight_sellers() -> None:
    aggregates = SellerAggregates(
        seller_ids=np.asarray(["many", "one"]),
        review_counts=np.asarray([4, 1]),
        rating_sums=np.asarray([20.0, 1.0]),
        mean_ratings=np.asarray([5.0, 1.0]),
        category_counts=np.asarray([[0, 0, 0, 0, 4], [1, 0, 0, 0, 0]]),
    )

    result = seller_equal_reference(aggregates)

    assert result["seller_count"] == 2
    assert result["mean_rating"] == pytest.approx(3.0)
    assert np.asarray(result["probabilities"]).sum() == pytest.approx(1.0)


def test_miss_classification_reports_both_error_directions() -> None:
    result = miss_classification(
        np.asarray([1.0, 3.0, 5.0]),
        np.asarray([2.0, 2.0, 2.0]),
        np.asarray([4.0, 4.0, 4.0]),
    )

    assert result.tolist() == ["below", "inside", "above"]


def test_miss_classification_rejects_invalid_intervals() -> None:
    with pytest.raises(ValueError, match="invalid"):
        miss_classification(np.asarray([3.0]), np.asarray([4.0]), np.asarray([2.0]))
