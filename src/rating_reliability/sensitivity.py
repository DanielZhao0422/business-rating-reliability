"""Preplanned Phase 3C sensitivity and robustness analyses."""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np
from numpy.typing import NDArray

from rating_reliability.evaluation import (
    EvaluationData,
    build_evaluation_data,
    error_metrics,
    metric_dict,
    paired_mae_bootstrap,
)
from rating_reliability.historical import (
    SellerAggregates,
    aggregate_sellers,
    pooled_reference,
)
from rating_reliability.ranking import ranking_comparison
from rating_reliability.statistical_config import StatisticalProtocol


def seller_equal_reference(aggregates: SellerAggregates) -> dict[str, object]:
    """Average each seller's categorical distribution with equal seller weight."""

    proportions = aggregates.category_counts / aggregates.review_counts[:, None]
    probabilities = proportions.mean(axis=0)
    probabilities = probabilities / probabilities.sum()
    mean = float(probabilities @ np.arange(1, 6, dtype=np.float64))
    return {
        "probabilities": probabilities,
        "mean_rating": mean,
        "seller_count": len(aggregates),
    }


def _scenario_row(
    scenario_id: str,
    purpose: str,
    data: EvaluationData,
    *,
    classification: str,
    cutoff: date,
    reference_definition: str,
    reference_mean: float,
    prior_strength: float,
    historical_minimum: int = 1,
    evaluation_minimum: int = 1,
    bootstrap_resamples: int | None = None,
    interval_level: float = 0.95,
    seed: int = 42,
) -> dict[str, object]:
    mask = (data.historical_counts >= historical_minimum) & (
        data.evaluation_counts >= evaluation_minimum
    )
    if not np.any(mask):
        raise ValueError(f"sensitivity scenario has no eligible sellers: {scenario_id}")
    raw = error_metrics(
        data.raw_estimates[mask],
        data.evaluation_means[mask],
        data.evaluation_counts[mask],
    )
    adjusted_values = (
        data.historical_category_counts[mask] @ np.arange(1, 6)
        + prior_strength * reference_mean
    ) / (data.historical_counts[mask] + prior_strength)
    adjusted = error_metrics(
        adjusted_values,
        data.evaluation_means[mask],
        data.evaluation_counts[mask],
    )
    improvement = raw.seller_equal_mae - adjusted.seller_equal_mae
    row: dict[str, object] = {
        "scenario_id": scenario_id,
        "classification": classification,
        "purpose": purpose,
        "cutoff": cutoff.isoformat(),
        "reference_definition": reference_definition,
        "reference_mean": reference_mean,
        "prior_strength": prior_strength,
        "historical_minimum": historical_minimum,
        "evaluation_minimum": evaluation_minimum,
        "seller_count": int(mask.sum()),
        "retained_proportion": float(mask.mean()),
        "historical_rating_count": int(data.historical_counts[mask].sum()),
        "evaluation_rating_count": int(data.evaluation_counts[mask].sum()),
        "raw": metric_dict(raw),
        "adjusted": metric_dict(adjusted),
        "mae_improvement": improvement,
        "relative_mae_improvement": (
            improvement / raw.seller_equal_mae if raw.seller_equal_mae else None
        ),
        "direction": (
            "adjusted_favored"
            if improvement > 0
            else "raw_favored"
            if improvement < 0
            else "no_difference"
        ),
    }
    if bootstrap_resamples:
        subset = EvaluationData(
            seller_ids=data.seller_ids[mask],
            historical_counts=data.historical_counts[mask],
            historical_category_counts=data.historical_category_counts[mask],
            raw_estimates=data.raw_estimates[mask],
            adjusted_estimates=adjusted_values,
            evaluation_counts=data.evaluation_counts[mask],
            evaluation_means=data.evaluation_means[mask],
        )
        row["paired_bootstrap"] = paired_mae_bootstrap(
            subset,
            resamples=bootstrap_resamples,
            interval_level=interval_level,
            seed=seed,
        )
    return row


def prior_strength_sensitivity(
    data: EvaluationData,
    *,
    reference_mean: float,
    cutoff: date,
    protocol: StatisticalProtocol,
) -> list[dict[str, object]]:
    """Describe final-period performance over the predefined prior grid."""

    return [
        _scenario_row(
            f"prior_m_{int(value)}",
            "Descriptive final-period prior-strength sensitivity; not retuning.",
            data,
            classification="preplanned_sensitivity",
            cutoff=cutoff,
            reference_definition="review_pooled",
            reference_mean=reference_mean,
            prior_strength=value,
        )
        for value in protocol.prior_strength_grid
    ]


def reference_sensitivity(
    data: EvaluationData,
    historical: SellerAggregates,
    *,
    pooled_mean: float,
    selected_m: float,
    cutoff: date,
    top_k: tuple[int, ...],
) -> list[dict[str, object]]:
    """Compare review-pooled and seller-equal historical references."""

    alternative = seller_equal_reference(historical)
    rows: list[dict[str, object]] = []
    for identifier, definition, mean in (
        ("reference_pooled", "review_pooled", pooled_mean),
        (
            "reference_seller_equal",
            "seller_equal_categorical",
            alternative["mean_rating"],
        ),
    ):
        row = _scenario_row(
            identifier,
            "Reference-weighting sensitivity with fixed primary prior strength.",
            data,
            classification="preplanned_sensitivity",
            cutoff=cutoff,
            reference_definition=definition,
            reference_mean=float(mean),
            prior_strength=selected_m,
        )
        alternative_estimates = (
            data.historical_category_counts @ np.arange(1, 6) + selected_m * float(mean)
        ) / (data.historical_counts + selected_m)
        row["ranking_vs_raw"] = ranking_comparison(
            data.raw_estimates, alternative_estimates, data.seller_ids, top_k
        )
        if definition == "seller_equal_categorical":
            row["reference_probabilities"] = alternative["probabilities"].tolist()
        rows.append(row)
    return rows


def count_threshold_sensitivity(
    data: EvaluationData,
    *,
    reference_mean: float,
    selected_m: float,
    cutoff: date,
    protocol: StatisticalProtocol,
) -> list[dict[str, object]]:
    """Evaluate locked and supplemental historical and future-count thresholds."""

    rows: list[dict[str, object]] = []
    historical_locked = set(protocol.sensitivity_historical_min_ratings)
    evaluation_locked = {
        protocol.evaluation_min_ratings,
        *protocol.secondary_evaluation_min_ratings,
    }
    for threshold in sorted(historical_locked | {50}):
        rows.append(
            _scenario_row(
                f"historical_min_{threshold}",
                "Historical evidence threshold; low-volume sellers remain in primary.",
                data,
                classification=(
                    "preplanned_sensitivity"
                    if threshold in historical_locked
                    else "supplemental_sensitivity"
                ),
                cutoff=cutoff,
                reference_definition="review_pooled",
                reference_mean=reference_mean,
                prior_strength=selected_m,
                historical_minimum=threshold,
                bootstrap_resamples=protocol.bootstrap_resamples,
                interval_level=protocol.interval_level,
                seed=protocol.random_seed,
            )
        )
    for threshold in sorted(evaluation_locked | {3, 20}):
        rows.append(
            _scenario_row(
                f"evaluation_min_{threshold}",
                (
                    "Retrospective future-outcome precision threshold; "
                    "not deployable eligibility."
                ),
                data,
                classification=(
                    "preplanned_sensitivity"
                    if threshold in evaluation_locked
                    else "supplemental_sensitivity"
                ),
                cutoff=cutoff,
                reference_definition="review_pooled",
                reference_mean=reference_mean,
                prior_strength=selected_m,
                evaluation_minimum=threshold,
                bootstrap_resamples=protocol.bootstrap_resamples,
                interval_level=protocol.interval_level,
                seed=protocol.random_seed,
            )
        )
    return rows


def cutoff_sensitivity(
    connection: duckdb.DuckDBPyConnection,
    *,
    cutoffs: tuple[date, ...],
    final_end: date,
    protocol: StatisticalProtocol,
) -> list[dict[str, object]]:
    """Evaluate alternative cutoffs under the locked fixed-grid/m=10 rule."""

    rows: list[dict[str, object]] = []
    for cutoff in cutoffs:
        historical = aggregate_sellers(connection, end_exclusive=cutoff)
        evaluation = aggregate_sellers(
            connection, start_inclusive=cutoff, end_exclusive=final_end
        )
        reference = pooled_reference(connection, end_exclusive=cutoff)
        base = build_evaluation_data(
            historical,
            evaluation,
            reference_mean=reference.mean_rating,
            prior_strength=10,
        )
        for value in protocol.prior_strength_grid:
            row = _scenario_row(
                f"cutoff_{cutoff.isoformat()}_m_{int(value)}",
                "Alternative-cutoff fixed-grid robustness; m is not outcome-selected.",
                base,
                classification="preplanned_sensitivity",
                cutoff=cutoff,
                reference_definition="review_pooled",
                reference_mean=reference.mean_rating,
                prior_strength=value,
                bootstrap_resamples=(
                    protocol.bootstrap_resamples if value == 10 else None
                ),
                interval_level=protocol.interval_level,
                seed=protocol.random_seed,
            )
            row["is_prespecified_summary"] = value == 10
            row["selection_rule"] = (
                "m=10 prespecified summary; complete grid reported without selection"
            )
            rows.append(row)
    return rows


def historical_variability_groups(
    data: EvaluationData,
    *,
    reference_mean: float,
    selected_m: float,
    cutoff: date,
    protocol: StatisticalProtocol,
) -> tuple[list[dict[str, object]], NDArray[np.str_]]:
    """Evaluate the protocol-defined historical variability groups."""

    counts = data.historical_counts
    stars = np.arange(1, 6, dtype=np.float64)
    sums = data.historical_category_counts @ stars
    squared_sums = data.historical_category_counts @ (stars**2)
    variances = np.full(len(data), np.nan)
    eligible = counts >= 2
    variances[eligible] = (
        squared_sums[eligible] - sums[eligible] ** 2 / counts[eligible]
    ) / (counts[eligible] - 1)
    positive = variances > 1e-12
    median_positive = float(np.median(variances[positive]))
    labels = np.full(len(data), "one_rating", dtype="U32")
    labels[eligible & ~positive] = "zero_variance"
    labels[positive & (variances <= median_positive)] = "positive_variance_lower"
    labels[positive & (variances > median_positive)] = "positive_variance_upper"
    rows: list[dict[str, object]] = []
    for label in (
        "one_rating",
        "zero_variance",
        "positive_variance_lower",
        "positive_variance_upper",
    ):
        mask = labels == label
        subset = EvaluationData(
            seller_ids=data.seller_ids[mask],
            historical_counts=data.historical_counts[mask],
            historical_category_counts=data.historical_category_counts[mask],
            raw_estimates=data.raw_estimates[mask],
            adjusted_estimates=data.adjusted_estimates[mask],
            evaluation_counts=data.evaluation_counts[mask],
            evaluation_means=data.evaluation_means[mask],
        )
        row = _scenario_row(
            f"variability_{label}",
            "Exploratory historical-only rating-variability diagnostic.",
            subset,
            classification="post_hoc_exploratory",
            cutoff=cutoff,
            reference_definition="review_pooled",
            reference_mean=reference_mean,
            prior_strength=selected_m,
            bootstrap_resamples=protocol.bootstrap_resamples,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed,
        )
        row["sample_variance_definition"] = (
            "undefined" if label == "one_rating" else "ddof=1"
        )
        row["positive_variance_median_boundary"] = median_positive
        rows.append(row)
    return rows, labels
