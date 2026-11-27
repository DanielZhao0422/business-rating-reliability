"""Noncausal Phase 3C operational outcome association analyses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
import numpy as np
from numpy.typing import NDArray

from rating_reliability.historical import SellerAggregates
from rating_reliability.ranking import spearman_correlation


@dataclass(frozen=True)
class OperationalCohort:
    """One row per future-initiated eligible reviewed order."""

    order_ids: NDArray[np.str_]
    seller_ids: NDArray[np.str_]
    statuses: NDArray[np.str_]
    delivery_delays: NDArray[np.float64]
    delivered_late: NDArray[np.float64]
    item_values: NDArray[np.float64]
    freight_values: NDArray[np.float64]
    historical_counts: NDArray[np.int64]
    raw_estimates: NDArray[np.float64]
    adjusted_estimates: NDArray[np.float64]

    def __len__(self) -> int:
        return len(self.order_ids)


def extract_operational_cohort(
    connection: duckdb.DuckDBPyConnection,
    historical: SellerAggregates,
    *,
    cutoff: date,
    reference_mean: float,
    prior_strength: float,
) -> OperationalCohort:
    """Extract future purchases with subsequent answers for historical sellers."""

    history_index = {
        seller: index for index, seller in enumerate(historical.seller_ids)
    }
    rows = connection.execute(
        """
        SELECT
            o.order_id,
            o.seller_id,
            coalesce(o.order_status, 'missing') AS order_status,
            o.delivery_delay_days::DOUBLE,
            CASE
                WHEN o.order_status = 'delivered'
                 AND o.actual_delivery_timestamp IS NOT NULL
                 AND o.estimated_delivery_timestamp IS NOT NULL
                THEN CAST(o.delivered_late AS DOUBLE)
            END AS delivered_late,
            o.total_item_value::DOUBLE,
            o.total_freight_value::DOUBLE
        FROM seller_operational_features AS o
        JOIN seller_rating_observations AS r USING (order_id)
        WHERE o.purchase_timestamp >= ?
          AND r.review_answer_timestamp >= ?
        ORDER BY o.order_id
        """,
        [cutoff, cutoff],
    ).fetchall()
    kept = [row for row in rows if str(row[1]) in history_index]
    seller_ids = np.asarray([str(row[1]) for row in kept], dtype=np.str_)
    indices = np.asarray([history_index[value] for value in seller_ids], dtype=np.int64)
    raw = historical.mean_ratings[indices]
    adjusted = (historical.rating_sums[indices] + prior_strength * reference_mean) / (
        historical.review_counts[indices] + prior_strength
    )

    def nullable_float(position: int) -> NDArray[np.float64]:
        return np.asarray(
            [np.nan if row[position] is None else float(row[position]) for row in kept],
            dtype=np.float64,
        )

    result = OperationalCohort(
        order_ids=np.asarray([str(row[0]) for row in kept], dtype=np.str_),
        seller_ids=seller_ids,
        statuses=np.asarray([str(row[2]) for row in kept], dtype=np.str_),
        delivery_delays=nullable_float(3),
        delivered_late=nullable_float(4),
        item_values=nullable_float(5),
        freight_values=nullable_float(6),
        historical_counts=historical.review_counts[indices],
        raw_estimates=raw,
        adjusted_estimates=adjusted,
    )
    if len(np.unique(result.order_ids)) != len(result):
        raise ValueError("operational extraction duplicated order IDs")
    sizes = {len(getattr(result, name)) for name in result.__dataclass_fields__}
    if sizes != {len(result)}:
        raise ValueError("operational cohort arrays have inconsistent lengths")
    return result


def seller_delivery_aggregates(cohort: OperationalCohort) -> dict[str, NDArray]:
    """Aggregate evaluable delivered outcomes once per seller."""

    mask = np.isfinite(cohort.delivered_late)
    sellers = np.unique(cohort.seller_ids[mask])
    late_counts = np.empty(len(sellers), dtype=np.int64)
    order_counts = np.empty(len(sellers), dtype=np.int64)
    mean_delay = np.empty(len(sellers), dtype=np.float64)
    raw = np.empty(len(sellers), dtype=np.float64)
    adjusted = np.empty(len(sellers), dtype=np.float64)
    history = np.empty(len(sellers), dtype=np.int64)
    for index, seller in enumerate(sellers):
        seller_mask = mask & (cohort.seller_ids == seller)
        order_counts[index] = int(seller_mask.sum())
        late_counts[index] = int(np.nansum(cohort.delivered_late[seller_mask]))
        mean_delay[index] = float(np.nanmean(cohort.delivery_delays[seller_mask]))
        first = int(np.flatnonzero(seller_mask)[0])
        raw[index] = cohort.raw_estimates[first]
        adjusted[index] = cohort.adjusted_estimates[first]
        history[index] = cohort.historical_counts[first]
    return {
        "seller_ids": sellers,
        "evaluable_orders": order_counts,
        "late_orders": late_counts,
        "late_rate": late_counts / order_counts,
        "mean_delay_days": mean_delay,
        "raw_estimate": raw,
        "adjusted_estimate": adjusted,
        "historical_count": history,
    }


def _pearson(first: NDArray[np.float64], second: NDArray[np.float64]) -> float:
    if np.std(first) == 0 or np.std(second) == 0:
        return float("nan")
    return float(np.corrcoef(first, second)[0, 1])


def _bootstrap_correlation(
    predictor: NDArray[np.float64],
    outcome: NDArray[np.float64],
    *,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    values = np.empty(resamples, dtype=np.float64)
    for index in range(resamples):
        sample = rng.integers(0, len(predictor), size=len(predictor))
        values[index] = spearman_correlation(predictor[sample], outcome[sample])
    finite = values[np.isfinite(values)]
    return tuple(float(value) for value in np.quantile(finite, [0.025, 0.975]))


def descriptive_associations(
    cohort: OperationalCohort, *, resamples: int, seed: int
) -> tuple[list[dict[str, object]], dict[str, NDArray]]:
    """Compare raw and adjusted estimates with seller-level later outcomes."""

    sellers = seller_delivery_aggregates(cohort)
    rows: list[dict[str, object]] = []
    for method, key in (("raw", "raw_estimate"), ("adjusted", "adjusted_estimate")):
        predictor = sellers[key]
        for outcome_name, outcome_key in (
            ("late_delivery_rate", "late_rate"),
            ("mean_delivery_delay_days", "mean_delay_days"),
        ):
            outcome = sellers[outcome_key]
            lower, upper = _bootstrap_correlation(
                predictor, outcome, resamples=resamples, seed=seed
            )
            rows.append(
                {
                    "analysis": "seller_level_correlation",
                    "method": method,
                    "outcome": outcome_name,
                    "seller_count": len(predictor),
                    "order_count": int(sellers["evaluable_orders"].sum()),
                    "spearman": spearman_correlation(predictor, outcome),
                    "spearman_bootstrap_lower": lower,
                    "spearman_bootstrap_upper": upper,
                    "pearson": _pearson(predictor, outcome),
                    "classification": "exploratory_noncausal",
                }
            )
    return rows, sellers


def rating_quartile_groups(
    sellers: dict[str, NDArray], method: str
) -> list[dict[str, object]]:
    """Compare outcomes across predictor-only empirical quartiles."""

    predictor = sellers[f"{method}_estimate"]
    boundaries = np.quantile(predictor, [0.25, 0.5, 0.75])
    groups = np.searchsorted(boundaries, predictor, side="right")
    rows = []
    for group in range(4):
        mask = groups == group
        if not np.any(mask):
            continue
        rows.append(
            {
                "analysis": "seller_rating_quartile",
                "method": method,
                "group": f"Q{group + 1}",
                "seller_count": int(mask.sum()),
                "evaluable_orders": int(sellers["evaluable_orders"][mask].sum()),
                "late_orders": int(sellers["late_orders"][mask].sum()),
                "late_rate": float(
                    sellers["late_orders"][mask].sum()
                    / sellers["evaluable_orders"][mask].sum()
                ),
                "mean_seller_late_rate": float(sellers["late_rate"][mask].mean()),
                "predictor_minimum": float(predictor[mask].min()),
                "predictor_maximum": float(predictor[mask].max()),
                "classification": "exploratory_noncausal",
            }
        )
    return rows


def _fit_logistic_clustered(
    design: NDArray[np.float64],
    outcome: NDArray[np.float64],
    clusters: NDArray[np.str_],
    names: tuple[str, ...],
) -> dict[str, object]:
    """Fit logistic MLE and a seller-cluster sandwich covariance."""

    x = np.asarray(design, dtype=np.float64)
    y = np.asarray(outcome, dtype=np.float64)
    beta = np.zeros(x.shape[1], dtype=np.float64)
    converged = False
    completed_iterations = 0
    for _ in range(100):
        completed_iterations += 1
        probability = 1 / (1 + np.exp(-np.clip(x @ beta, -35, 35)))
        weights = np.maximum(probability * (1 - probability), 1e-12)
        information = x.T @ (weights[:, None] * x)
        score = x.T @ (y - probability)
        step = np.linalg.solve(information, score)
        beta += step
        if np.max(np.abs(step)) < 1e-10:
            converged = True
            break
    if not converged:
        raise ValueError("logistic regression did not converge")
    probability = 1 / (1 + np.exp(-np.clip(x @ beta, -35, 35)))
    weights = np.maximum(probability * (1 - probability), 1e-12)
    bread = np.linalg.inv(x.T @ (weights[:, None] * x))
    meat = np.zeros((x.shape[1], x.shape[1]), dtype=np.float64)
    unique_clusters = np.unique(clusters)
    for cluster in unique_clusters:
        mask = clusters == cluster
        cluster_score = x[mask].T @ (y[mask] - probability[mask])
        meat += np.outer(cluster_score, cluster_score)
    correction = (len(unique_clusters) / (len(unique_clusters) - 1)) * (
        (len(y) - 1) / (len(y) - x.shape[1])
    )
    covariance = correction * bread @ meat @ bread
    standard_errors = np.sqrt(np.diag(covariance))
    coefficients = []
    for name, estimate, standard_error in zip(
        names, beta, standard_errors, strict=True
    ):
        coefficients.append(
            {
                "term": name,
                "log_odds": float(estimate),
                "cluster_standard_error": float(standard_error),
                "odds_ratio": float(np.exp(estimate)),
                "odds_ratio_ci_lower": float(np.exp(estimate - 1.96 * standard_error)),
                "odds_ratio_ci_upper": float(np.exp(estimate + 1.96 * standard_error)),
            }
        )
    return {
        "converged": converged,
        "iterations": completed_iterations,
        "order_count": len(y),
        "seller_clusters": len(unique_clusters),
        "coefficients": coefficients,
    }


def logistic_associations(cohort: OperationalCohort) -> list[dict[str, object]]:
    """Fit fair raw/adjusted order-level models on one evaluable cohort."""

    mask = np.isfinite(cohort.delivered_late)
    y = cohort.delivered_late[mask]
    clusters = cohort.seller_ids[mask]
    history = np.log1p(cohort.historical_counts[mask].astype(np.float64))
    history = (history - history.mean()) / history.std()
    item = np.log1p(cohort.item_values[mask])
    freight = np.log1p(cohort.freight_values[mask])
    item = (item - item.mean()) / item.std()
    freight = (freight - freight.mean()) / freight.std()
    rows = []
    for method, predictor_all in (
        ("raw", cohort.raw_estimates),
        ("adjusted", cohort.adjusted_estimates),
    ):
        predictor = predictor_all[mask]
        predictor = (predictor - predictor.mean()) / predictor.std()
        for model, columns, names in (
            (
                "historical_predictors",
                (predictor, history),
                ("intercept", "rating_per_sd", "log_historical_count_per_sd"),
            ),
            (
                "contextual_order_controls",
                (predictor, history, item, freight),
                (
                    "intercept",
                    "rating_per_sd",
                    "log_historical_count_per_sd",
                    "log_item_value_per_sd",
                    "log_freight_value_per_sd",
                ),
            ),
        ):
            design = np.column_stack((np.ones(len(y)), *columns))
            result = _fit_logistic_clustered(design, y, clusters, names)
            result.update(
                {
                    "analysis": "order_level_logistic_late_delivery",
                    "method": method,
                    "model": model,
                    "classification": "exploratory_noncausal",
                    "context_note": (
                        "Item and freight values are contemporaneous future-order "
                        "characteristics, not cutoff-time seller predictors."
                        if model == "contextual_order_controls"
                        else "All predictors are available from pre-cutoff history."
                    ),
                }
            )
            rows.append(result)
    return rows


def operational_population_summary(
    connection: duckdb.DuckDBPyConnection,
    cohort: OperationalCohort,
    *,
    cutoff: date,
    historical_sellers: int,
    evaluation_sellers: int,
    paired_sellers: int,
) -> dict[str, object]:
    """Report outcome denominators, missingness, selection, and logical bounds."""

    status_values, status_counts = np.unique(cohort.statuses, return_counts=True)
    evaluable = np.isfinite(cohort.delivered_late)
    delivered = cohort.statuses == "delivered"
    unresolved_delivered = delivered & ~evaluable
    late = int(np.nansum(cohort.delivered_late[evaluable]))
    evaluable_count = int(evaluable.sum())
    unresolved = int(unresolved_delivered.sum())
    bound_denominator = evaluable_count + unresolved
    categories = connection.execute(
        """
        WITH future AS (SELECT unnest(?) AS order_id), counts AS (
            SELECT f.order_id, count(c.product_category) AS category_count
            FROM future AS f
            LEFT JOIN order_product_categories AS c USING (order_id)
            GROUP BY f.order_id
        )
        SELECT count(*) FILTER (WHERE category_count = 0),
               count(*) FILTER (WHERE category_count > 1)
        FROM counts
        """,
        [cohort.order_ids.tolist()],
    ).fetchone()
    source_orders, eligible_orders = connection.execute(
        "SELECT (SELECT count(*) FROM source_orders), "
        "(SELECT count(*) FROM seller_rating_observations)"
    ).fetchone()
    reasons = {
        str(reason): int(count)
        for reason, count in connection.execute(
            "SELECT primary_reason, count(*) FROM order_eligibility GROUP BY 1"
        ).fetchall()
    }
    return {
        "future_operational_cohort": {
            "orders": len(cohort),
            "sellers": len(np.unique(cohort.seller_ids)),
            "purchase_date_rule": f"purchase_timestamp >= {cutoff.isoformat()}",
            "rating_availability_rule": (
                f"review_answer_timestamp >= {cutoff.isoformat()}"
            ),
            "status_distribution": {
                str(value): int(count)
                for value, count in zip(status_values, status_counts, strict=True)
            },
            "delivered_orders": int(delivered.sum()),
            "evaluable_lateness_orders": evaluable_count,
            "unresolved_delivered_orders": unresolved,
            "observed_late_orders": late,
            "observed_late_rate": late / evaluable_count,
            "missing_delivery_bounds": {
                "denominator": bound_denominator,
                "lower_bound_all_unresolved_non_late": late / bound_denominator,
                "upper_bound_all_unresolved_late": (late + unresolved)
                / bound_denominator,
                "cancelled_and_non_delivered_excluded": True,
            },
            "delay_days": {
                "mean": float(np.nanmean(cohort.delivery_delays[evaluable])),
                "median": float(np.nanmedian(cohort.delivery_delays[evaluable])),
                "p90": float(np.nanquantile(cohort.delivery_delays[evaluable], 0.9)),
            },
            "category_missing_orders": int(categories[0]),
            "multi_category_orders": int(categories[1]),
        },
        "selection": {
            "source_orders": int(source_orders),
            "eligible_seller_rating_orders": int(eligible_orders),
            "eligible_proportion": float(eligible_orders / source_orders),
            "primary_exclusion_reasons": reasons,
            "historical_sellers": historical_sellers,
            "evaluation_sellers": evaluation_sellers,
            "both_period_sellers": paired_sellers,
            "historical_only_sellers": historical_sellers - paired_sellers,
            "evaluation_only_sellers": evaluation_sellers - paired_sellers,
        },
    }
