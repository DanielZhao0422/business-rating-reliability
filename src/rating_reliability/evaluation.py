"""Chronological tuning, predictive metrics, and paired bootstrap inference."""

from __future__ import annotations

import math
from dataclasses import dataclass

import duckdb
import numpy as np
from numpy.typing import NDArray

from rating_reliability.historical import (
    SellerAggregates,
    StatisticalDataError,
    aggregate_sellers,
    pooled_reference,
)
from rating_reliability.statistical_config import StatisticalProtocol


@dataclass(frozen=True)
class EvaluationData:
    """Paired historical predictions and later seller outcomes."""

    seller_ids: NDArray[np.str_]
    historical_counts: NDArray[np.int64]
    historical_category_counts: NDArray[np.int64]
    raw_estimates: NDArray[np.float64]
    adjusted_estimates: NDArray[np.float64]
    evaluation_counts: NDArray[np.int64]
    evaluation_means: NDArray[np.float64]

    def __len__(self) -> int:
        return len(self.seller_ids)


@dataclass(frozen=True)
class ErrorMetrics:
    """Four locked predictive error metrics."""

    seller_equal_mae: float
    seller_equal_rmse: float
    review_weighted_mae: float
    review_weighted_rmse: float


def adjusted_estimates(
    aggregates: SellerAggregates, reference_mean: float, prior_strength: float
) -> NDArray[np.float64]:
    """Vectorized locked shrinkage estimator for seller aggregates."""

    if not math.isfinite(prior_strength) or prior_strength <= 0:
        raise ValueError("prior_strength must be positive and finite")
    if not math.isfinite(reference_mean) or not 1 <= reference_mean <= 5:
        raise ValueError("reference_mean must be finite and between 1 and 5")
    numerator = aggregates.rating_sums + prior_strength * reference_mean
    result = numerator / (aggregates.review_counts + prior_strength)
    lower = np.minimum(aggregates.mean_ratings, reference_mean)
    upper = np.maximum(aggregates.mean_ratings, reference_mean)
    if np.any(result < lower - 1e-12) or np.any(result > upper + 1e-12):
        raise StatisticalDataError("adjusted estimate is outside its shrinkage bounds")
    return result.astype(np.float64, copy=False)


def _paired_indices(
    earlier: SellerAggregates, later: SellerAggregates
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.str_]]:
    sellers, earlier_index, later_index = np.intersect1d(
        earlier.seller_ids, later.seller_ids, assume_unique=True, return_indices=True
    )
    return earlier_index, later_index, sellers


def tune_prior_strength(
    connection: duckdb.DuckDBPyConnection, protocol: StatisticalProtocol
) -> dict[str, object]:
    """Select prior strength using only locked pre-cutoff chronological folds."""

    candidates = np.asarray(protocol.prior_strength_grid, dtype=np.float64)
    all_errors: list[list[NDArray[np.float64]]] = [[] for _ in candidates]
    folds_report: list[dict[str, object]] = []
    total_units = 0
    adequate = True
    for fold in protocol.internal_validation_folds:
        training = aggregate_sellers(
            connection, end_exclusive=fold.training_end_exclusive
        )
        validation = aggregate_sellers(
            connection,
            start_inclusive=fold.validation_start_inclusive,
            end_exclusive=fold.validation_end_exclusive,
        )
        reference = pooled_reference(
            connection, end_exclusive=fold.training_end_exclusive
        )
        train_index, validation_index, sellers = _paired_indices(training, validation)
        seller_count = len(sellers)
        total_units += seller_count
        if seller_count < protocol.minimum_sellers_per_fold:
            adequate = False
        candidate_mae: dict[str, float] = {}
        for candidate_index, candidate in enumerate(candidates):
            predictions = adjusted_estimates(
                training, reference.mean_rating, float(candidate)
            )[train_index]
            errors = np.abs(predictions - validation.mean_ratings[validation_index])
            all_errors[candidate_index].append(errors)
            candidate_mae[str(_display_strength(candidate))] = float(errors.mean())
        folds_report.append(
            {
                "name": fold.name,
                "training_end_exclusive": fold.training_end_exclusive.isoformat(),
                "validation_start_inclusive": (
                    fold.validation_start_inclusive.isoformat()
                ),
                "validation_end_exclusive": fold.validation_end_exclusive.isoformat(),
                "training_ratings": reference.observation_count,
                "training_sellers": len(training),
                "validation_ratings": int(validation.review_counts.sum()),
                "validation_sellers": len(validation),
                "eligible_seller_fold_units": seller_count,
                "reference_mean": reference.mean_rating,
                "candidate_mae": candidate_mae,
            }
        )
    if total_units < protocol.minimum_seller_fold_units:
        adequate = False

    aggregate_scores = np.asarray(
        [
            np.concatenate(candidate_errors).mean()
            if candidate_errors and any(len(values) for values in candidate_errors)
            else np.nan
            for candidate_errors in all_errors
        ],
        dtype=np.float64,
    )
    if np.any(~np.isfinite(aggregate_scores)):
        adequate = False

    fallback_used = not adequate
    if fallback_used:
        selected = protocol.prior_strength_fallback
        reason = (
            "locked validation adequacy thresholds were not met: "
            f"minimum {protocol.minimum_sellers_per_fold} sellers per fold and "
            f"{protocol.minimum_seller_fold_units} total seller-fold units"
        )
        tie_candidates: list[float] = []
    else:
        minimum = float(aggregate_scores.min())
        tied = candidates[
            np.abs(aggregate_scores - minimum) <= protocol.prior_tie_tolerance
        ]
        selected = float(tied.min())
        tie_candidates = [float(value) for value in tied]
        reason = None
    if selected <= 0:
        raise StatisticalDataError("selected prior strength is not positive")
    return {
        "selection_metric": protocol.prior_tuning_metric,
        "aggregation_unit": "seller_fold_equal_weight",
        "candidate_strengths": [
            _display_strength(value) for value in protocol.prior_strength_grid
        ],
        "folds": folds_report,
        "aggregate_validation_mae": {
            str(_display_strength(candidate)): float(score)
            for candidate, score in zip(candidates, aggregate_scores, strict=True)
        },
        "selected_prior_strength": _display_strength(selected),
        "fallback_used": fallback_used,
        "fallback_reason": reason,
        "tie_tolerance": protocol.prior_tie_tolerance,
        "tie_break": protocol.prior_tie_break,
        "tie_candidates": [_display_strength(value) for value in tie_candidates],
        "total_seller_fold_units": total_units,
        "final_evaluation_accessed": False,
    }


def _display_strength(value: float) -> int | float:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else numeric


def build_evaluation_data(
    historical: SellerAggregates,
    evaluation: SellerAggregates,
    *,
    reference_mean: float,
    prior_strength: float,
) -> EvaluationData:
    """Pair both methods on exactly the same final-evaluation seller universe."""

    history_index, evaluation_index, sellers = _paired_indices(historical, evaluation)
    adjusted = adjusted_estimates(historical, reference_mean, prior_strength)
    result = EvaluationData(
        seller_ids=sellers,
        historical_counts=historical.review_counts[history_index],
        historical_category_counts=historical.category_counts[history_index],
        raw_estimates=historical.mean_ratings[history_index],
        adjusted_estimates=adjusted[history_index],
        evaluation_counts=evaluation.review_counts[evaluation_index],
        evaluation_means=evaluation.mean_ratings[evaluation_index],
    )
    if len(result) == 0:
        raise StatisticalDataError("no sellers are represented in both periods")
    sizes = {
        len(result.historical_counts),
        len(result.historical_category_counts),
        len(result.raw_estimates),
        len(result.adjusted_estimates),
        len(result.evaluation_counts),
        len(result.evaluation_means),
        len(result.seller_ids),
    }
    if len(sizes) != 1:
        raise StatisticalDataError("evaluation methods do not share one population")
    return result


def error_metrics(
    estimates: NDArray[np.float64],
    outcomes: NDArray[np.float64],
    outcome_counts: NDArray[np.int64],
) -> ErrorMetrics:
    """Calculate locked seller-equal and review-weighted error metrics."""

    if not (len(estimates) == len(outcomes) == len(outcome_counts)) or not len(
        estimates
    ):
        raise ValueError("metric arrays must be nonempty and equal length")
    if np.any(outcome_counts <= 0):
        raise ValueError("evaluation counts must be positive")
    errors = estimates - outcomes
    absolute = np.abs(errors)
    squared = errors**2
    total_weight = float(outcome_counts.sum())
    return ErrorMetrics(
        seller_equal_mae=float(absolute.mean()),
        seller_equal_rmse=float(np.sqrt(squared.mean())),
        review_weighted_mae=float(np.dot(absolute, outcome_counts) / total_weight),
        review_weighted_rmse=float(
            np.sqrt(np.dot(squared, outcome_counts) / total_weight)
        ),
    )


def metric_dict(metrics: ErrorMetrics) -> dict[str, float]:
    """Convert metrics to a stable report mapping."""

    return {
        "seller_equal_mae": metrics.seller_equal_mae,
        "seller_equal_rmse": metrics.seller_equal_rmse,
        "review_weighted_mae": metrics.review_weighted_mae,
        "review_weighted_rmse": metrics.review_weighted_rmse,
    }


def volume_group(count: int) -> str:
    """Return the locked review-count band label."""

    if count <= 0:
        raise ValueError("review count must be positive")
    if count <= 4:
        return "1-4"
    if count <= 9:
        return "5-9"
    if count <= 19:
        return "10-19"
    if count <= 49:
        return "20-49"
    return "50+"


def metrics_by_group(data: EvaluationData, *, group_by: str) -> list[dict[str, object]]:
    """Calculate both methods over locked historical or evaluation volume bands."""

    if group_by == "historical":
        counts = data.historical_counts
    elif group_by == "evaluation":
        counts = data.evaluation_counts
    else:
        raise ValueError("group_by must be 'historical' or 'evaluation'")
    labels = np.asarray([volume_group(int(value)) for value in counts])
    rows: list[dict[str, object]] = []
    for group in ("1-4", "5-9", "10-19", "20-49", "50+"):
        mask = labels == group
        if not np.any(mask):
            continue
        raw = error_metrics(
            data.raw_estimates[mask],
            data.evaluation_means[mask],
            data.evaluation_counts[mask],
        )
        adjusted = error_metrics(
            data.adjusted_estimates[mask],
            data.evaluation_means[mask],
            data.evaluation_counts[mask],
        )
        rows.append(
            {
                "group_by": group_by,
                "volume_group": group,
                "seller_count": int(mask.sum()),
                "historical_rating_count": int(data.historical_counts[mask].sum()),
                "evaluation_rating_count": int(data.evaluation_counts[mask].sum()),
                "raw": metric_dict(raw),
                "adjusted": metric_dict(adjusted),
                "mae_improvement": raw.seller_equal_mae - adjusted.seller_equal_mae,
            }
        )
    return rows


def paired_mae_bootstrap(
    data: EvaluationData,
    *,
    resamples: int,
    interval_level: float,
    seed: int,
) -> dict[str, object]:
    """Bootstrap the paired seller-level MAE improvement."""

    if resamples <= 0:
        raise ValueError("resamples must be positive")
    if not 0 < interval_level < 1:
        raise ValueError("interval_level must be between zero and one")
    raw_absolute = np.abs(data.raw_estimates - data.evaluation_means)
    adjusted_absolute = np.abs(data.adjusted_estimates - data.evaluation_means)
    differences = raw_absolute - adjusted_absolute
    rng = np.random.default_rng(seed)
    draws = np.empty(resamples, dtype=np.float64)
    batch_size = min(256, resamples)
    for start in range(0, resamples, batch_size):
        stop = min(start + batch_size, resamples)
        indices = rng.integers(0, len(data), size=(stop - start, len(data)))
        draws[start:stop] = differences[indices].mean(axis=1)
    tail = (1 - interval_level) / 2
    lower, upper = np.quantile(draws, [tail, 1 - tail])
    return {
        "unit": "seller",
        "preserve_pairing": True,
        "resamples": resamples,
        "seed": seed,
        "interval_method": "percentile",
        "interval_level": interval_level,
        "seller_count": len(data),
        "observed_mae_improvement": float(differences.mean()),
        "confidence_interval_lower": float(lower),
        "confidence_interval_upper": float(upper),
    }
