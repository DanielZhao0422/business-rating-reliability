"""Fixed-universe ranking comparison and posterior rank stability."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

from rating_reliability.uncertainty import UncertaintyError


def average_ranks(values: NDArray[np.float64]) -> NDArray[np.float64]:
    """Rank descending values with one-based average ranks for exact ties."""

    scores = np.asarray(values, dtype=np.float64)
    if scores.ndim != 1 or not len(scores) or np.any(~np.isfinite(scores)):
        raise ValueError("ranking values must be a nonempty finite vector")
    order = np.argsort(-scores, kind="stable")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=np.float64)
    start = 0
    while start < len(scores):
        stop = start + 1
        while stop < len(scores) and sorted_scores[stop] == sorted_scores[start]:
            stop += 1
        ranks[order[start:stop]] = (start + 1 + stop) / 2
        start = stop
    return ranks


def spearman_correlation(
    first: NDArray[np.float64], second: NDArray[np.float64]
) -> float:
    """Pearson correlation of average ranks."""

    first_ranks = average_ranks(first)
    second_ranks = average_ranks(second)
    if np.std(first_ranks) == 0 or np.std(second_ranks) == 0:
        return math.nan
    return float(np.corrcoef(first_ranks, second_ranks)[0, 1])


def kendall_tau_b(first: NDArray[np.float64], second: NDArray[np.float64]) -> float:
    """Calculate Kendall's tau-b, including exact tie correction."""

    x = np.asarray(first, dtype=np.float64)
    y = np.asarray(second, dtype=np.float64)
    if x.shape != y.shape or x.ndim != 1 or len(x) < 2:
        raise ValueError(
            "Kendall inputs must be equal vectors with at least two values"
        )
    concordant = discordant = tie_x_only = tie_y_only = 0
    for index in range(len(x) - 1):
        dx = np.sign(x[index] - x[index + 1 :])
        dy = np.sign(y[index] - y[index + 1 :])
        products = dx * dy
        concordant += int(np.count_nonzero(products > 0))
        discordant += int(np.count_nonzero(products < 0))
        tie_x_only += int(np.count_nonzero((dx == 0) & (dy != 0)))
        tie_y_only += int(np.count_nonzero((dy == 0) & (dx != 0)))
    denominator = math.sqrt(
        (concordant + discordant + tie_x_only) * (concordant + discordant + tie_y_only)
    )
    if denominator == 0:
        return math.nan
    return (concordant - discordant) / denominator


def deterministic_top_k(
    values: NDArray[np.float64], seller_ids: NDArray[np.str_], k: int
) -> NDArray[np.str_]:
    """Select top K by score descending and seller ID ascending at ties."""

    scores = np.asarray(values, dtype=np.float64)
    sellers = np.asarray(seller_ids, dtype=np.str_)
    if scores.shape != sellers.shape or scores.ndim != 1:
        raise ValueError("ranking scores and seller IDs must be equal vectors")
    if k <= 0 or k > len(scores):
        raise ValueError("top-k must be positive and no larger than the universe")
    order = np.lexsort((sellers, -scores))
    return sellers[order[:k]]


def ranking_comparison(
    raw: NDArray[np.float64],
    adjusted: NDArray[np.float64],
    seller_ids: NDArray[np.str_],
    top_k_values: tuple[int, ...],
) -> dict[str, object]:
    """Compare raw and adjusted rankings over one fixed seller universe."""

    if not (len(raw) == len(adjusted) == len(seller_ids)):
        raise ValueError("ranking methods must use the same seller universe")
    raw_ranks = average_ranks(raw)
    adjusted_ranks = average_ranks(adjusted)
    absolute_changes = np.abs(raw_ranks - adjusted_ranks)
    spearman = spearman_correlation(raw, adjusted)
    kendall = kendall_tau_b(raw, adjusted)
    top_k: dict[str, object] = {}
    for k in top_k_values:
        raw_top = set(deterministic_top_k(raw, seller_ids, k).tolist())
        adjusted_top = set(deterministic_top_k(adjusted, seller_ids, k).tolist())
        raw_boundary = np.sort(raw)[-k]
        adjusted_boundary = np.sort(adjusted)[-k]
        top_k[str(k)] = {
            "intersection_count": len(raw_top & adjusted_top),
            "overlap_proportion": len(raw_top & adjusted_top) / k,
            "raw_boundary_tie_count": int(np.count_nonzero(raw == raw_boundary)),
            "adjusted_boundary_tie_count": int(
                np.count_nonzero(adjusted == adjusted_boundary)
            ),
        }
    return {
        "seller_count": len(raw),
        "tie_method": "average_rank",
        "top_k_boundary_policy": "estimate_desc_then_seller_id_asc",
        "spearman_correlation": spearman if math.isfinite(spearman) else None,
        "kendall_tau_b": kendall if math.isfinite(kendall) else None,
        "absolute_rank_change": {
            "mean": float(absolute_changes.mean()),
            "median": float(np.median(absolute_changes)),
            "maximum": float(absolute_changes.max()),
            "p90": float(np.quantile(absolute_changes, 0.9)),
        },
        "top_k": top_k,
    }


def posterior_ranking_stability(
    alphas: NDArray[np.float64],
    seller_ids: NDArray[np.str_],
    *,
    draws: int,
    top_k_values: tuple[int, ...],
    seed: int,
    batch_size: int = 200,
) -> dict[str, object]:
    """Summarize posterior-draw ranks without retaining expected-rating draws."""

    values = np.asarray(alphas, dtype=np.float64)
    sellers = np.asarray(seller_ids, dtype=np.str_)
    if values.ndim != 2 or values.shape != (len(sellers), 5):
        raise UncertaintyError("ranking alphas must have shape (sellers, 5)")
    if np.any(values < 0) or np.any(values.sum(axis=1) <= 0):
        raise UncertaintyError("ranking alpha rows must have positive total mass")
    if draws <= 0 or batch_size <= 0:
        raise ValueError("draw and batch counts must be positive")
    if any(k > len(sellers) for k in top_k_values):
        raise ValueError("top-k exceeds ranking universe")

    seller_order = np.argsort(sellers, kind="stable")
    if not np.array_equal(seller_order, np.arange(len(sellers))):
        raise ValueError("seller IDs must be sorted for deterministic tie handling")
    ranks = np.empty((draws, len(sellers)), dtype=np.float32)
    top_counts = {k: np.zeros(len(sellers), dtype=np.int64) for k in top_k_values}
    stars = np.arange(1, 6, dtype=np.float64)
    rng = np.random.default_rng(seed)
    for start in range(0, draws, batch_size):
        stop = min(start + batch_size, draws)
        current = stop - start
        gamma = rng.gamma(
            shape=values[np.newaxis, :, :],
            scale=1.0,
            size=(current, len(sellers), 5),
        )
        totals = gamma.sum(axis=2, keepdims=True)
        if np.any(totals <= 0):
            raise UncertaintyError("posterior gamma draw has zero total mass")
        expected = (gamma / totals) @ stars
        orders = np.argsort(-expected, axis=1, kind="stable")
        for local_index in range(current):
            order = orders[local_index]
            draw_ranks = np.empty(len(sellers), dtype=np.float32)
            draw_ranks[order] = np.arange(1, len(sellers) + 1, dtype=np.float32)
            sorted_values = expected[local_index, order]
            tied = np.flatnonzero(np.diff(sorted_values) == 0)
            if len(tied):
                group_start = 0
                while group_start < len(sellers):
                    group_stop = group_start + 1
                    while (
                        group_stop < len(sellers)
                        and sorted_values[group_stop] == sorted_values[group_start]
                    ):
                        group_stop += 1
                    if group_stop - group_start > 1:
                        draw_ranks[order[group_start:group_stop]] = (
                            group_start + 1 + group_stop
                        ) / 2
                    group_start = group_stop
            ranks[start + local_index] = draw_ranks
            for k in top_k_values:
                top_counts[k][order[:k]] += 1

    lower, median, upper = np.quantile(ranks, [0.025, 0.5, 0.975], axis=0)
    widths = upper - lower
    top_summary: dict[str, object] = {}
    for k, counts in top_counts.items():
        probabilities = counts / draws
        top_summary[str(k)] = {
            "mean_top_k_probability": float(probabilities.mean()),
            "median_top_k_probability": float(np.median(probabilities)),
            "maximum_top_k_probability": float(probabilities.max()),
            "sellers_with_probability_at_least_0_5": int(
                np.count_nonzero(probabilities >= 0.5)
            ),
            "sellers_with_probability_at_least_0_9": int(
                np.count_nonzero(probabilities >= 0.9)
            ),
        }
    return {
        "draws": draws,
        "seed": seed,
        "seller_count": len(sellers),
        "rank_interval_level": 0.95,
        "rank_interval_width": {
            "mean": float(widths.mean()),
            "median": float(np.median(widths)),
            "p90": float(np.quantile(widths, 0.9)),
            "maximum": float(widths.max()),
        },
        "posterior_median_rank": {
            "minimum": float(median.min()),
            "maximum": float(median.max()),
        },
        "top_k_probability_summary": top_summary,
        "interpretation": (
            "Model-conditional ranking uncertainty for seller-associated order "
            "experience ratings; not objective probability of seller quality."
        ),
    }


def tie_diagnostics(
    raw_values: NDArray[np.float64], historical_counts: NDArray[np.int64]
) -> dict[str, object]:
    """Describe exact raw-score ties overall and by locked volume group."""

    values = np.asarray(raw_values, dtype=np.float64)
    counts = np.asarray(historical_counts, dtype=np.int64)
    if values.shape != counts.shape or values.ndim != 1:
        raise ValueError("raw values and historical counts must be equal vectors")
    unique, inverse, frequencies = np.unique(
        values, return_inverse=True, return_counts=True
    )
    tied = frequencies[inverse] > 1

    def band(value: int) -> str:
        if value <= 4:
            return "1-4"
        if value <= 9:
            return "5-9"
        if value <= 19:
            return "10-19"
        if value <= 49:
            return "20-49"
        return "50+"

    by_volume = []
    labels = np.asarray([band(int(value)) for value in counts])
    for label in ("1-4", "5-9", "10-19", "20-49", "50+"):
        mask = labels == label
        by_volume.append(
            {
                "volume_group": label,
                "seller_count": int(mask.sum()),
                "sellers_in_ties": int(np.count_nonzero(tied & mask)),
                "tie_proportion": float(np.mean(tied[mask])) if np.any(mask) else None,
            }
        )
    return {
        "seller_count": len(values),
        "distinct_raw_values": len(unique),
        "tie_groups": int(np.count_nonzero(frequencies > 1)),
        "sellers_in_ties": int(tied.sum()),
        "sellers_in_ties_proportion": float(tied.mean()),
        "largest_tie_group": int(frequencies.max()),
        "by_historical_volume": by_volume,
    }


def tie_aware_top_k(
    raw_values: NDArray[np.float64],
    adjusted_values: NDArray[np.float64],
    seller_ids: NDArray[np.str_],
    k: int,
) -> dict[str, object]:
    """Bound top-K overlap over every ordering of the raw boundary tie."""

    raw = np.asarray(raw_values, dtype=np.float64)
    adjusted = np.asarray(adjusted_values, dtype=np.float64)
    sellers = np.asarray(seller_ids, dtype=np.str_)
    if not (raw.shape == adjusted.shape == sellers.shape) or raw.ndim != 1:
        raise ValueError("tie-aware rankings require one fixed seller universe")
    if k <= 0 or k > len(raw):
        raise ValueError("k must be positive and within the seller universe")
    boundary = np.sort(raw)[-k]
    definite = set(sellers[raw > boundary].tolist())
    tied = set(sellers[raw == boundary].tolist())
    slots = k - len(definite)
    adjusted_top = set(deterministic_top_k(adjusted, sellers, k).tolist())
    definite_overlap = len(definite & adjusted_top)
    tied_overlap = len(tied & adjusted_top)
    minimum = definite_overlap + max(0, slots - (len(tied) - tied_overlap))
    maximum = definite_overlap + min(slots, tied_overlap)
    deterministic_raw = set(deterministic_top_k(raw, sellers, k).tolist())
    actual = len(deterministic_raw & adjusted_top)
    expected = definite_overlap + slots * tied_overlap / len(tied)
    return {
        "k": k,
        "raw_boundary_value": float(boundary),
        "definite_raw_members": len(definite),
        "raw_boundary_tie_candidates": len(tied),
        "boundary_slots": slots,
        "adjusted_top_k_members_in_raw_boundary_tie": tied_overlap,
        "deterministic_overlap_count": actual,
        "minimum_possible_overlap_count": minimum,
        "maximum_possible_overlap_count": maximum,
        "fractional_expected_overlap_count": expected,
        "deterministic_overlap_proportion": actual / k,
        "minimum_possible_overlap_proportion": minimum / k,
        "maximum_possible_overlap_proportion": maximum / k,
        "fractional_expected_overlap_proportion": expected / k,
        "fractional_assumption": "uniform random ordering within the raw boundary tie",
    }


def pairwise_posterior_reliability(
    alphas: NDArray[np.float64],
    historical_counts: NDArray[np.int64],
    raw_values: NDArray[np.float64],
    adjusted_values: NDArray[np.float64],
    *,
    pair_sample: int,
    draws: int,
    seed: int,
    batch_size: int = 100,
) -> dict[str, object]:
    """Estimate ordering support on a uniform deterministic sample of seller pairs."""

    values = np.asarray(alphas, dtype=np.float64)
    counts = np.asarray(historical_counts, dtype=np.int64)
    raw = np.asarray(raw_values, dtype=np.float64)
    adjusted = np.asarray(adjusted_values, dtype=np.float64)
    seller_count = len(values)
    if values.shape != (seller_count, 5) or not (
        counts.shape == raw.shape == adjusted.shape == (seller_count,)
    ):
        raise ValueError("pairwise inputs have inconsistent shapes")
    left_all, right_all = np.triu_indices(seller_count, 1)
    total_pairs = len(left_all)
    sample_size = min(pair_sample, total_pairs)
    rng = np.random.default_rng(seed)
    chosen = rng.choice(total_pairs, size=sample_size, replace=False)
    left = left_all[chosen]
    right = right_all[chosen]
    wins = np.zeros(sample_size, dtype=np.int64)
    stars = np.arange(1, 6, dtype=np.float64)
    for start in range(0, draws, batch_size):
        stop = min(start + batch_size, draws)
        current = stop - start
        gamma = rng.gamma(
            shape=values[np.newaxis, :, :],
            scale=1.0,
            size=(current, seller_count, 5),
        )
        expected = (gamma / gamma.sum(axis=2, keepdims=True)) @ stars
        wins += np.count_nonzero(expected[:, left] > expected[:, right], axis=0)
    probabilities = wins / draws
    support = np.maximum(probabilities, 1 - probabilities)
    raw_order = np.sign(raw[left] - raw[right])
    adjusted_order = np.sign(adjusted[left] - adjusted[right])
    disagreement = raw_order != adjusted_order
    low_pair = np.minimum(counts[left], counts[right])
    return {
        "seller_universe": seller_count,
        "total_possible_pairs": total_pairs,
        "sampled_pairs": sample_size,
        "posterior_draws": draws,
        "seed": seed,
        "strong_support_at_least_0_90": float(np.mean(support >= 0.90)),
        "strong_support_at_least_0_95": float(np.mean(support >= 0.95)),
        "median_ordering_support": float(np.median(support)),
        "raw_adjusted_pairwise_disagreement": float(np.mean(disagreement)),
        "raw_tied_pair_proportion": float(np.mean(raw_order == 0)),
        "support_by_minimum_historical_count": [
            {
                "minimum_count_group": label,
                "pair_count": int(mask.sum()),
                "strong_support_at_least_0_90": float(np.mean(support[mask] >= 0.90)),
                "median_ordering_support": float(np.median(support[mask])),
            }
            for label, mask in (
                ("1-4", low_pair <= 4),
                ("5-19", (low_pair >= 5) & (low_pair <= 19)),
                ("20+", low_pair >= 20),
            )
            if np.any(mask)
        ],
        "interpretation": (
            "Uniform sampled-pair, model-conditional ordering probabilities; "
            "not probabilities of objective seller superiority."
        ),
    }
