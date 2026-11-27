"""Phase 3C posterior predictive calibration diagnostics."""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
from numpy.typing import NDArray

from rating_reliability.evaluation import EvaluationData, volume_group
from rating_reliability.historical import aggregate_sellers, pooled_reference
from rating_reliability.uncertainty import (
    posterior_alpha,
    predictive_intervals,
    summarize_intervals,
)


def miss_classification(
    observed: NDArray[np.float64],
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
) -> NDArray[np.str_]:
    """Classify observations as below, inside, or above an interval."""

    values = np.asarray(observed, dtype=np.float64)
    low = np.asarray(lower, dtype=np.float64)
    high = np.asarray(upper, dtype=np.float64)
    if not (values.shape == low.shape == high.shape):
        raise ValueError("calibration arrays must have identical shapes")
    if np.any(low > high) or np.any(~np.isfinite(values)):
        raise ValueError("calibration intervals or observations are invalid")
    result = np.full(len(values), "inside", dtype="U8")
    result[values < low] = "below"
    result[values > high] = "above"
    return result


def _group_rows(
    labels: NDArray[np.str_],
    lower: NDArray[np.float64],
    upper: NDArray[np.float64],
    observed: NDArray[np.float64],
    dimension: str,
) -> list[dict[str, object]]:
    classes = miss_classification(observed, lower, upper)
    rows: list[dict[str, object]] = []
    for label in np.unique(labels):
        mask = labels == label
        widths = upper[mask] - lower[mask]
        covered = classes[mask] == "inside"
        rows.append(
            {
                "dimension": dimension,
                "group": str(label),
                "seller_units": int(mask.sum()),
                "coverage": float(covered.mean()),
                "coverage_standard_error": float(
                    np.sqrt(covered.mean() * (1 - covered.mean()) / mask.sum())
                ),
                "mean_width": float(widths.mean()),
                "median_width": float(np.median(widths)),
                "below_count": int(np.count_nonzero(classes[mask] == "below")),
                "inside_count": int(np.count_nonzero(classes[mask] == "inside")),
                "above_count": int(np.count_nonzero(classes[mask] == "above")),
            }
        )
    return rows


def primary_calibration_diagnostics(
    data: EvaluationData,
    reference_probabilities: NDArray[np.float64],
    *,
    prior_strength: float,
    draws: int,
    interval_level: float,
    seed: int,
    variability_labels: NDArray[np.str_],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Reproduce and extend primary predictive calibration diagnostics."""

    alphas = posterior_alpha(
        data.historical_category_counts, reference_probabilities, prior_strength
    )
    intervals = predictive_intervals(
        alphas,
        data.evaluation_counts,
        data.evaluation_means,
        draws=draws,
        interval_level=interval_level,
        seed=seed,
    )
    classes = miss_classification(
        data.evaluation_means, intervals.lower, intervals.upper
    )
    historical_labels = np.asarray(
        [volume_group(int(value)) for value in data.historical_counts]
    )
    evaluation_labels = np.asarray(
        [volume_group(int(value)) for value in data.evaluation_counts]
    )
    rows = _group_rows(
        historical_labels,
        intervals.lower,
        intervals.upper,
        data.evaluation_means,
        "historical_volume",
    )
    rows.extend(
        _group_rows(
            evaluation_labels,
            intervals.lower,
            intervals.upper,
            data.evaluation_means,
            "evaluation_volume",
        )
    )
    rows.extend(
        _group_rows(
            variability_labels,
            intervals.lower,
            intervals.upper,
            data.evaluation_means,
            "historical_variability",
        )
    )
    summary = {
        "nominal_coverage": interval_level,
        "posterior_draws": draws,
        "seed": seed,
        "conditional_on_realized_future_sample_size": True,
        "seller_count": len(data),
        "overall": summarize_intervals(
            intervals.lower, intervals.upper, intervals.covered
        ),
        "miss_direction": {
            "below": int(np.count_nonzero(classes == "below")),
            "inside": int(np.count_nonzero(classes == "inside")),
            "above": int(np.count_nonzero(classes == "above")),
        },
    }
    return summary, rows


def calibration_prior_sensitivity(
    data: EvaluationData,
    reference_probabilities: NDArray[np.float64],
    *,
    prior_grid: tuple[float, ...],
    draws: int,
    interval_level: float,
    seed: int,
) -> list[dict[str, object]]:
    """Compare coverage and width over the predefined prior grid."""

    rows = []
    for offset, strength in enumerate(prior_grid):
        alphas = posterior_alpha(
            data.historical_category_counts, reference_probabilities, strength
        )
        intervals = predictive_intervals(
            alphas,
            data.evaluation_counts,
            data.evaluation_means,
            draws=draws,
            interval_level=interval_level,
            seed=seed + offset,
        )
        rows.append(
            {
                "dimension": "prior_strength_exploratory",
                "group": str(int(strength)),
                "seller_units": len(data),
                **summarize_intervals(
                    intervals.lower, intervals.upper, intervals.covered
                ),
                "draws": draws,
                "note": "Coverage-width sensitivity; no final-period recalibration.",
            }
        )
    return rows


def temporal_calibration(
    connection: duckdb.DuckDBPyConnection,
    historical_data: EvaluationData,
    reference_probabilities: NDArray[np.float64],
    *,
    prior_strength: float,
    draws: int,
    interval_level: float,
    seed: int,
) -> list[dict[str, object]]:
    """Evaluate future sample-mean coverage in calendar evaluation windows."""

    windows = (
        ("2018_Q1", date(2018, 1, 1), date(2018, 4, 1)),
        ("2018_Q2", date(2018, 4, 1), date(2018, 7, 1)),
        ("2018_Q3_partial", date(2018, 7, 1), date(2018, 9, 1)),
    )
    rows: list[dict[str, object]] = []
    for offset, (label, start, end) in enumerate(windows):
        period = aggregate_sellers(connection, start_inclusive=start, end_exclusive=end)
        sellers, history_index, period_index = np.intersect1d(
            historical_data.seller_ids,
            period.seller_ids,
            assume_unique=True,
            return_indices=True,
        )
        alphas = posterior_alpha(
            historical_data.historical_category_counts[history_index],
            reference_probabilities,
            prior_strength,
        )
        intervals = predictive_intervals(
            alphas,
            period.review_counts[period_index],
            period.mean_ratings[period_index],
            draws=draws,
            interval_level=interval_level,
            seed=seed + offset,
        )
        classes = miss_classification(
            period.mean_ratings[period_index], intervals.lower, intervals.upper
        )
        rows.append(
            {
                "dimension": "evaluation_time",
                "group": label,
                "seller_units": len(sellers),
                "evaluation_ratings": int(period.review_counts[period_index].sum()),
                **summarize_intervals(
                    intervals.lower, intervals.upper, intervals.covered
                ),
                "below_count": int(np.count_nonzero(classes == "below")),
                "inside_count": int(np.count_nonzero(classes == "inside")),
                "above_count": int(np.count_nonzero(classes == "above")),
            }
        )
    return rows


def temporal_shift_diagnostics(
    connection: duckdb.DuckDBPyConnection,
    data: EvaluationData,
    *,
    cutoff: date,
    end: date,
) -> dict[str, object]:
    """Describe marketplace and paired-seller historical-to-future shifts."""

    historical_reference = pooled_reference(connection, end_exclusive=cutoff)
    evaluation_reference = pooled_reference(
        connection, start_inclusive=cutoff, end_exclusive=end
    )
    seller_shift = data.evaluation_means - data.raw_estimates
    return {
        "historical_marketplace": {
            "rating_count": historical_reference.observation_count,
            "probabilities": historical_reference.probabilities.tolist(),
            "mean": historical_reference.mean_rating,
        },
        "evaluation_marketplace": {
            "rating_count": evaluation_reference.observation_count,
            "probabilities": evaluation_reference.probabilities.tolist(),
            "mean": evaluation_reference.mean_rating,
        },
        "marketplace_mean_change": (
            evaluation_reference.mean_rating - historical_reference.mean_rating
        ),
        "paired_seller_mean_change": {
            "seller_count": len(data),
            "mean": float(seller_shift.mean()),
            "median": float(np.median(seller_shift)),
            "negative_proportion": float(np.mean(seller_shift < 0)),
            "positive_proportion": float(np.mean(seller_shift > 0)),
        },
        "interpretation": (
            "Descriptive temporal shift; it does not identify the cause of "
            "predictive undercoverage."
        ),
    }
