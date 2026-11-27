"""Synthetic tests for Phase 3C tie-aware ranking diagnostics."""

from __future__ import annotations

import numpy as np
import pytest

from rating_reliability.ranking import (
    pairwise_posterior_reliability,
    tie_aware_top_k,
    tie_diagnostics,
)


def test_tie_diagnostics_handles_all_tied_and_no_ties() -> None:
    counts = np.asarray([1, 5, 10], dtype=np.int64)
    all_tied = tie_diagnostics(np.asarray([5.0, 5.0, 5.0]), counts)
    no_ties = tie_diagnostics(np.asarray([3.0, 4.0, 5.0]), counts)

    assert all_tied["distinct_raw_values"] == 1
    assert all_tied["largest_tie_group"] == 3
    assert all_tied["sellers_in_ties"] == 3
    assert no_ties["tie_groups"] == 0
    assert no_ties["sellers_in_ties"] == 0


def test_tie_aware_top_k_bounds_and_fractional_membership() -> None:
    raw = np.asarray([5.0, 4.0, 4.0, 4.0, 3.0])
    adjusted = np.asarray([4.1, 4.4, 4.3, 4.2, 3.0])
    sellers = np.asarray(["a", "b", "c", "d", "e"])

    result = tie_aware_top_k(raw, adjusted, sellers, 2)

    assert result["raw_boundary_tie_candidates"] == 3
    assert result["boundary_slots"] == 1
    assert result["minimum_possible_overlap_count"] == 0
    assert result["maximum_possible_overlap_count"] == 1
    assert result["fractional_expected_overlap_count"] == pytest.approx(2 / 3)


def test_pairwise_posterior_reliability_is_reproducible() -> None:
    alphas = np.asarray([[1, 1, 1, 1, 8], [8, 1, 1, 1, 1]], dtype=float)
    kwargs = {
        "pair_sample": 10,
        "draws": 200,
        "seed": 42,
        "batch_size": 25,
    }
    first = pairwise_posterior_reliability(
        alphas,
        np.asarray([5, 5]),
        np.asarray([5.0, 1.0]),
        np.asarray([4.0, 2.0]),
        **kwargs,
    )
    second = pairwise_posterior_reliability(
        alphas,
        np.asarray([5, 5]),
        np.asarray([5.0, 1.0]),
        np.asarray([4.0, 2.0]),
        **kwargs,
    )

    assert first == second
    assert first["sampled_pairs"] == 1
    assert first["median_ordering_support"] > 0.9
