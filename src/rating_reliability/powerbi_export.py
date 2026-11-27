"""Configuration-driven, privacy-gated local exports for Power BI Desktop."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from rating_reliability.artifacts import sha256_file
from rating_reliability.evaluation import (
    adjusted_estimates,
    build_evaluation_data,
    error_metrics,
    volume_group,
)
from rating_reliability.historical import (
    aggregate_sellers,
    assert_window_integrity,
    pooled_reference,
)
from rating_reliability.operational import extract_operational_cohort
from rating_reliability.powerbi_config import (
    PowerBIExportSpec,
    TableSpec,
    load_powerbi_export_spec,
)
from rating_reliability.powerbi_pseudonymization import (
    key_version,
    pseudonymize_sellers,
    secret_from_environment,
)
from rating_reliability.powerbi_validation import (
    PowerBIValidationError,
    assert_close,
    validate_privacy,
    validate_relationships,
    validate_table_rows,
)
from rating_reliability.ranking import average_ranks
from rating_reliability.statistical_config import (
    StatisticalProtocol,
    load_statistical_protocol,
)
from rating_reliability.uncertainty import (
    credible_intervals,
    posterior_alpha,
    predictive_intervals,
)

PIPELINE_VERSION = "1.0.0"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PowerBIValidationError(
            f"cannot read required artifact {path.name}"
        ) from error
    if not isinstance(value, dict):
        raise PowerBIValidationError(f"required artifact {path.name} is not an object")
    return value


def _csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            return list(csv.DictReader(source))
    except OSError as error:
        raise PowerBIValidationError(
            f"cannot read required artifact {path.name}"
        ) from error


def _number(value: str | None, *, integer: bool = False) -> int | float | None:
    if value is None or value == "":
        return None
    return int(float(value)) if integer else float(value)


def _boolean(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def _ordered(table: TableSpec, values: Mapping[str, object]) -> dict[str, object]:
    return {column: values.get(column) for column in table.columns}


def _table_rows(
    spec: PowerBIExportSpec, name: str, rows: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    table = spec.table_by_name[name]
    return [_ordered(table, row) for row in rows]


def _scenario_family(scenario_id: str) -> str:
    for prefix, family in (
        ("prior_", "prior_strength"),
        ("reference_", "reference_weighting"),
        ("historical_min_", "historical_count_threshold"),
        ("evaluation_min_", "evaluation_count_threshold"),
        ("cutoff_", "historical_cutoff"),
        ("variability_", "historical_variability"),
    ):
        if scenario_id.startswith(prefix):
            return family
    raise PowerBIValidationError(f"unrecognized sensitivity scenario {scenario_id!r}")


def _scenario_rows(phase3c_dir: Path) -> list[dict[str, str]]:
    files = (
        "prior_sensitivity.csv",
        "reference_sensitivity.csv",
        "count_threshold_sensitivity.csv",
        "cutoff_sensitivity.csv",
        "variability_sensitivity.csv",
    )
    rows = [row for name in files for row in _csv(phase3c_dir / name)]
    keys = [row["scenario_id"] for row in rows]
    if len(keys) != len(set(keys)):
        raise PowerBIValidationError("Phase 3C sensitivity scenario IDs are not unique")
    return rows


def _top_k_probabilities(
    alphas: np.ndarray,
    top_ks: tuple[int, ...],
    *,
    draws: int,
    seed: int,
    batch_size: int = 500,
) -> dict[int, np.ndarray]:
    """Return model-conditional top-K inclusion probabilities without huge storage."""

    rng = np.random.default_rng(seed)
    stars = np.arange(1, 6, dtype=np.float64)
    counts = {k: np.zeros(len(alphas), dtype=np.int64) for k in top_ks}
    for start in range(0, draws, batch_size):
        size = min(batch_size, draws - start)
        scores = np.empty((size, len(alphas)), dtype=np.float64)
        for seller, alpha in enumerate(alphas):
            scores[:, seller] = rng.dirichlet(alpha, size=size) @ stars
        for k in top_ks:
            members = np.argpartition(-scores, k - 1, axis=1)[:, :k]
            counts[k] += np.bincount(members.ravel(), minlength=len(alphas))
    return {k: value.astype(np.float64) / draws for k, value in counts.items()}


def _ensure_inputs(
    database: Path,
    config: Path,
    export_spec: Path,
    phase3b_dir: Path,
    phase3c_dir: Path,
) -> None:
    for path in (database, config, export_spec):
        if not path.is_file():
            raise PowerBIValidationError(f"required input file is missing: {path}")
    for directory in (phase3b_dir, phase3c_dir):
        if not directory.is_dir():
            raise PowerBIValidationError(
                f"required artifact directory is missing: {directory}"
            )


def _database_end(connection: duckdb.DuckDBPyConnection) -> date:
    value = connection.execute(
        "SELECT max(review_answer_timestamp) FROM seller_rating_observations"
    ).fetchone()[0]
    if value is None:
        raise PowerBIValidationError("rating table is empty")
    return value.date() + timedelta(days=1)


def build_export_tables(
    connection: duckdb.DuckDBPyConnection,
    spec: PowerBIExportSpec,
    protocol: StatisticalProtocol,
    phase3b_dir: Path,
    phase3c_dir: Path,
    secret: bytes,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, object]]:
    """Reconstruct the locked analytical model and build all approved table rows."""

    model = _json(phase3b_dir / "model_comparison.json")
    ranking = _json(phase3b_dir / "ranking_comparison.json")
    uncertainty = _json(phase3b_dir / "uncertainty_summary.json")
    ranking3c = _json(phase3c_dir / "ranking_stability_summary.json")
    calibration = _json(phase3c_dir / "calibration_summary.json")
    population = _json(phase3c_dir / "population_and_missingness.json")
    selected_m = float(model["selected_prior_strength"])
    if selected_m <= 0:
        raise PowerBIValidationError("selected prior strength is invalid")

    split = assert_window_integrity(connection, cutoff=protocol.primary_cutoff)
    historical = aggregate_sellers(connection, end_exclusive=protocol.primary_cutoff)
    evaluation = aggregate_sellers(
        connection,
        start_inclusive=protocol.primary_cutoff,
        end_exclusive=_database_end(connection),
    )
    reference = pooled_reference(connection, end_exclusive=protocol.primary_cutoff)
    paired = build_evaluation_data(
        historical,
        evaluation,
        reference_mean=reference.mean_rating,
        prior_strength=selected_m,
    )
    adjusted_all = adjusted_estimates(historical, reference.mean_rating, selected_m)
    alphas_all = posterior_alpha(
        historical.category_counts, reference.probabilities, selected_m
    )
    credible = credible_intervals(
        alphas_all,
        draws=protocol.posterior_draws,
        interval_level=protocol.interval_level,
        seed=protocol.random_seed,
    )
    paired_alphas = posterior_alpha(
        paired.historical_category_counts, reference.probabilities, selected_m
    )
    predictive = predictive_intervals(
        paired_alphas,
        paired.evaluation_counts,
        paired.evaluation_means,
        draws=protocol.posterior_draws,
        interval_level=protocol.interval_level,
        seed=protocol.random_seed + 1,
    )
    top_probabilities = _top_k_probabilities(
        paired_alphas,
        protocol.ranking_top_k,
        draws=protocol.posterior_draws,
        seed=protocol.random_seed + 2,
    )

    all_source_ids = sorted(
        set(historical.seller_ids.tolist()) | set(evaluation.seller_ids.tolist())
    )
    seller_keys = pseudonymize_sellers(all_source_ids, secret)
    history_lookup = {
        seller: index for index, seller in enumerate(historical.seller_ids)
    }
    paired_lookup = {seller: index for index, seller in enumerate(paired.seller_ids)}
    raw_ranks = average_ranks(paired.raw_estimates)
    adjusted_ranks = average_ranks(paired.adjusted_estimates)

    cohort = extract_operational_cohort(
        connection,
        historical,
        cutoff=protocol.primary_cutoff,
        reference_mean=reference.mean_rating,
        prior_strength=selected_m,
    )
    operational_sellers = set(cohort.seller_ids.tolist())

    tables: dict[str, list[dict[str, object]]] = {}
    tables["dim_method"] = _table_rows(
        spec,
        "dim_method",
        [
            {
                "method_key": "raw",
                "method_label": "Raw average",
                "method_order": 1,
                "method_description": "Historical arithmetic mean",
                "color_hex": "#4E79A7",
            },
            {
                "method_key": "adjusted",
                "method_label": "Adjusted rating",
                "method_order": 2,
                "method_description": "Historical empirical-Bayes-style estimate",
                "color_hex": "#F28E2B",
            },
        ],
    )
    bands = (
        ("1-4", 1, 4),
        ("5-9", 5, 9),
        ("10-19", 10, 19),
        ("20-49", 20, 49),
        ("50+", 50, None),
    )
    tables["dim_review_volume"] = _table_rows(
        spec,
        "dim_review_volume",
        [
            {
                "volume_group_key": label,
                "lower_bound": lower,
                "upper_bound": upper,
                "sort_order": order,
                "display_label": label,
            }
            for order, (label, lower, upper) in enumerate(bands, start=1)
        ],
    )

    scenarios = _scenario_rows(phase3c_dir)
    scenario_dimension: list[dict[str, object]] = []
    sensitivity_fact: list[dict[str, object]] = []
    for order, row in enumerate(scenarios, start=1):
        scenario_id = row["scenario_id"]
        cutoff = date.fromisoformat(row["cutoff"]) if row.get("cutoff") else None
        scenario_dimension.append(
            {
                "scenario_key": scenario_id,
                "scenario_family": _scenario_family(scenario_id),
                "scenario_label": scenario_id.replace("_", " ").title(),
                "classification": row["classification"],
                "is_primary": scenario_id == "historical_min_1",
                "is_prespecified_summary": _boolean(row.get("is_prespecified_summary")),
                "cutoff": cutoff,
                "sort_order": order,
                "interpretation_note": row.get("purpose", ""),
            }
        )
        sensitivity_fact.append(
            {
                "scenario_key": scenario_id,
                "seller_count": _number(row["seller_count"], integer=True),
                "historical_rating_count": _number(
                    row["historical_rating_count"], integer=True
                ),
                "evaluation_rating_count": _number(
                    row["evaluation_rating_count"], integer=True
                ),
                "retained_proportion": _number(row["retained_proportion"]),
                "cutoff": cutoff,
                "prior_strength": _number(row.get("prior_strength")),
                "reference_mean": _number(row.get("reference_mean")),
                "raw_mae": _number(row["raw_mae"]),
                "adjusted_mae": _number(row["adjusted_mae"]),
                "mae_improvement": _number(row["mae_improvement"]),
                "relative_mae_improvement": _number(row["relative_mae_improvement"]),
                "raw_rmse": _number(row["raw_rmse"]),
                "adjusted_rmse": _number(row["adjusted_rmse"]),
                "bootstrap_lower": _number(row.get("bootstrap_lower")),
                "bootstrap_upper": _number(row.get("bootstrap_upper")),
                "direction": row["direction"],
            }
        )
    tables["dim_scenario"] = _table_rows(spec, "dim_scenario", scenario_dimension)
    tables["fact_sensitivity_scenario"] = _table_rows(
        spec, "fact_sensitivity_scenario", sensitivity_fact
    )

    tables["dim_seller"] = _table_rows(
        spec,
        "dim_seller",
        [
            {
                "seller_key": seller_keys[str(seller)],
                "seller_label": f"Seller {seller_keys[str(seller)][:8].upper()}",
                "historical_review_count": int(historical.review_counts[index]),
                "volume_group_key": volume_group(int(historical.review_counts[index])),
                "has_evaluation_rating": str(seller) in paired_lookup,
                "has_forward_operational_order": str(seller) in operational_sellers,
            }
            for index, seller in enumerate(historical.seller_ids)
        ],
    )

    estimate_rows: list[dict[str, object]] = []
    for seller, index in history_lookup.items():
        pair_index = paired_lookup.get(seller)
        for method in ("raw", "adjusted"):
            is_adjusted = method == "adjusted"
            estimate_rows.append(
                {
                    "seller_key": seller_keys[seller],
                    "method_key": method,
                    "estimate": float(
                        adjusted_all[index]
                        if is_adjusted
                        else historical.mean_ratings[index]
                    ),
                    "reference_mean": reference.mean_rating if is_adjusted else None,
                    "prior_strength": selected_m if is_adjusted else None,
                    "adjustment_signed": float(
                        adjusted_all[index] - historical.mean_ratings[index]
                    )
                    if is_adjusted
                    else 0.0,
                    "adjustment_absolute": float(
                        abs(adjusted_all[index] - historical.mean_ratings[index])
                    )
                    if is_adjusted
                    else 0.0,
                    "expected_rating_interval_lower": float(credible.lower[index])
                    if is_adjusted
                    else None,
                    "expected_rating_interval_upper": float(credible.upper[index])
                    if is_adjusted
                    else None,
                    "average_rank": (
                        float(
                            adjusted_ranks[pair_index]
                            if is_adjusted
                            else raw_ranks[pair_index]
                        )
                        if pair_index is not None
                        else None
                    ),
                    "top_10_probability": (
                        float(top_probabilities[10][pair_index])
                        if is_adjusted and pair_index is not None
                        else None
                    ),
                    "top_25_probability": (
                        float(top_probabilities[25][pair_index])
                        if is_adjusted and pair_index is not None
                        else None
                    ),
                    "top_50_probability": (
                        float(top_probabilities[50][pair_index])
                        if is_adjusted and pair_index is not None
                        else None
                    ),
                    "ranking_universe": "primary_evaluation_sellers"
                    if pair_index is not None
                    else None,
                }
            )
    tables["fact_seller_estimate"] = _table_rows(
        spec, "fact_seller_estimate", estimate_rows
    )

    evaluation_rows = []
    for index, seller in enumerate(paired.seller_ids):
        raw_error = float(paired.raw_estimates[index] - paired.evaluation_means[index])
        adjusted_error = float(
            paired.adjusted_estimates[index] - paired.evaluation_means[index]
        )
        miss = (
            "inside"
            if predictive.covered[index]
            else (
                "below"
                if paired.evaluation_means[index] < predictive.lower[index]
                else "above"
            )
        )
        evaluation_rows.append(
            {
                "seller_key": seller_keys[str(seller)],
                "evaluation_review_count": int(paired.evaluation_counts[index]),
                "evaluation_mean_rating": float(paired.evaluation_means[index]),
                "raw_estimate": float(paired.raw_estimates[index]),
                "adjusted_estimate": float(paired.adjusted_estimates[index]),
                "raw_error": raw_error,
                "adjusted_error": adjusted_error,
                "raw_absolute_error": abs(raw_error),
                "adjusted_absolute_error": abs(adjusted_error),
                "raw_squared_error": raw_error**2,
                "adjusted_squared_error": adjusted_error**2,
                "predictive_interval_lower": float(predictive.lower[index]),
                "predictive_interval_upper": float(predictive.upper[index]),
                "predictive_covered": bool(predictive.covered[index]),
                "predictive_miss_direction": miss,
            }
        )
    tables["fact_seller_evaluation"] = _table_rows(
        spec, "fact_seller_evaluation", evaluation_rows
    )

    volume_rows = []
    for row in _csv(phase3b_dir / "evaluation_by_volume.csv"):
        volume_rows.append(
            {
                "group_basis": row["group_by"],
                "volume_group_key": row["volume_group"],
                "seller_count": _number(row["seller_count"], integer=True),
                "historical_rating_count": _number(
                    row["historical_rating_count"], integer=True
                ),
                "evaluation_rating_count": _number(
                    row["evaluation_rating_count"], integer=True
                ),
                "raw_mae": _number(row["raw_seller_equal_mae"]),
                "adjusted_mae": _number(row["adjusted_seller_equal_mae"]),
                "mae_improvement": _number(row["mae_improvement"]),
                "raw_rmse": _number(row["raw_seller_equal_rmse"]),
                "adjusted_rmse": _number(row["adjusted_seller_equal_rmse"]),
                "raw_review_weighted_mae": _number(row["raw_review_weighted_mae"]),
                "adjusted_review_weighted_mae": _number(
                    row["adjusted_review_weighted_mae"]
                ),
                "raw_review_weighted_rmse": _number(row["raw_review_weighted_rmse"]),
                "adjusted_review_weighted_rmse": _number(
                    row["adjusted_review_weighted_rmse"]
                ),
            }
        )
    tables["fact_evaluation_volume"] = _table_rows(
        spec, "fact_evaluation_volume", volume_rows
    )

    comparison = ranking["method_comparison"]
    pairwise = ranking3c["pairwise_reliability"]
    tie_by_k = {int(row["k"]): row for row in ranking3c["tie_aware_top_k"]}
    ranking_rows = []
    for k in protocol.ranking_top_k:
        tie = tie_by_k[k]
        ranking_rows.append(
            {
                "top_k": k,
                "seller_universe": int(comparison["seller_count"]),
                "spearman": float(comparison["spearman_correlation"]),
                "kendall_tau_b": float(comparison["kendall_tau_b"]),
                "mean_absolute_rank_change": float(
                    comparison["absolute_rank_change"]["mean"]
                ),
                "deterministic_overlap_count": int(tie["deterministic_overlap_count"]),
                "minimum_overlap_count": int(tie["minimum_possible_overlap_count"]),
                "maximum_overlap_count": int(tie["maximum_possible_overlap_count"]),
                "fractional_overlap_count": float(
                    tie["fractional_expected_overlap_count"]
                ),
                "boundary_tie_candidates": int(tie["raw_boundary_tie_candidates"]),
                "sampled_pairs": int(pairwise["sampled_pairs"]),
                "posterior_draws": int(pairwise["posterior_draws"]),
                "strong_ordering_support_90": float(
                    pairwise["strong_support_at_least_0_90"]
                ),
            }
        )
    tables["fact_ranking_summary"] = _table_rows(
        spec, "fact_ranking_summary", ranking_rows
    )

    calibration_rows = []
    primary_calibration = calibration["primary"]
    primary_overall = primary_calibration["overall"]
    primary_miss = primary_calibration["miss_direction"]
    calibration_rows.append(
        {
            "calibration_dimension": "overall",
            "calibration_group": "all_evaluation_sellers",
            "seller_units": int(primary_calibration["seller_count"]),
            "coverage": float(primary_overall["empirical_coverage"]),
            "coverage_standard_error": None,
            "mean_width": float(primary_overall["mean_width"]),
            "median_width": float(primary_overall["median_width"]),
            "below_count": int(primary_miss["below"]),
            "inside_count": int(primary_miss["inside"]),
            "above_count": int(primary_miss["above"]),
            "nominal_coverage": float(primary_calibration["nominal_coverage"]),
            "draws": int(primary_calibration["posterior_draws"]),
            "note": "Primary conditional predictive coverage",
        }
    )
    for row in _csv(phase3c_dir / "calibration_diagnostics.csv"):
        coverage = row.get("coverage") or row.get("empirical_coverage")
        calibration_rows.append(
            {
                "calibration_dimension": row["dimension"],
                "calibration_group": row["group"],
                "seller_units": _number(row["seller_units"], integer=True),
                "coverage": _number(coverage),
                "coverage_standard_error": _number(row.get("coverage_standard_error")),
                "mean_width": _number(row["mean_width"]),
                "median_width": _number(row["median_width"]),
                "below_count": _number(row.get("below_count"), integer=True),
                "inside_count": _number(row.get("inside_count"), integer=True),
                "above_count": _number(row.get("above_count"), integer=True),
                "nominal_coverage": float(primary_calibration["nominal_coverage"]),
                "draws": _number(row.get("draws"), integer=True),
                "note": row.get("note") or None,
            }
        )
    tables["fact_calibration"] = _table_rows(spec, "fact_calibration", calibration_rows)

    operational_rows = []
    for seller in sorted(operational_sellers):
        mask = cohort.seller_ids == seller
        evaluable = mask & np.isfinite(cohort.delivered_late)
        delay = cohort.delivery_delays[evaluable]
        item = cohort.item_values[mask]
        freight = cohort.freight_values[mask]
        evaluable_count = int(evaluable.sum())
        late_count = int(np.nansum(cohort.delivered_late[evaluable]))
        operational_rows.append(
            {
                "seller_key": seller_keys[seller],
                "forward_order_count": int(mask.sum()),
                "delivered_order_count": int(
                    np.sum(cohort.statuses[mask] == "delivered")
                ),
                "evaluable_delivery_count": evaluable_count,
                "late_delivery_count": late_count,
                "late_delivery_rate": late_count / evaluable_count
                if evaluable_count
                else None,
                "mean_delivery_delay_days": float(np.mean(delay))
                if len(delay)
                else None,
                "mean_item_value": float(np.nanmean(item))
                if np.any(np.isfinite(item))
                else None,
                "mean_freight_value": float(np.nanmean(freight))
                if np.any(np.isfinite(freight))
                else None,
            }
        )
    tables["fact_seller_operational"] = _table_rows(
        spec, "fact_seller_operational", operational_rows
    )

    status_counts = Counter(cohort.statuses.tolist())
    tables["fact_operational_status"] = _table_rows(
        spec,
        "fact_operational_status",
        [
            {
                "order_status": status,
                "order_count": count,
                "order_proportion": count / len(cohort),
            }
            for status, count in sorted(status_counts.items())
        ],
    )

    associations = _csv(phase3c_dir / "operational_associations.csv")
    association_rows = []
    quartile_rows = []
    for row in associations:
        if row["analysis"] == "seller_level_correlation":
            association_rows.append(
                {
                    "method_key": row["method"],
                    "outcome_key": row["outcome"],
                    "seller_count": _number(row["seller_count"], integer=True),
                    "order_count": _number(row["order_count"], integer=True),
                    "pearson": _number(row["pearson"]),
                    "spearman": _number(row["spearman"]),
                    "spearman_ci_lower": _number(row["spearman_bootstrap_lower"]),
                    "spearman_ci_upper": _number(row["spearman_bootstrap_upper"]),
                    "classification": row["classification"],
                    "interpretation_note": (
                        "Observational, noncausal seller-level association"
                    ),
                }
            )
        elif row["analysis"] == "seller_rating_quartile":
            quartile_rows.append(
                {
                    "method_key": row["method"],
                    "quartile_key": row["group"],
                    "seller_count": _number(row["seller_count"], integer=True),
                    "evaluable_order_count": _number(
                        row["evaluable_orders"], integer=True
                    ),
                    "late_order_count": _number(row["late_orders"], integer=True),
                    "late_delivery_rate": _number(row["late_rate"]),
                    "mean_seller_late_rate": _number(row["mean_seller_late_rate"]),
                    "predictor_minimum": _number(row["predictor_minimum"]),
                    "predictor_maximum": _number(row["predictor_maximum"]),
                    "classification": row["classification"],
                }
            )
    tables["fact_operational_association"] = _table_rows(
        spec, "fact_operational_association", association_rows
    )
    tables["fact_operational_quartile"] = _table_rows(
        spec, "fact_operational_quartile", quartile_rows
    )

    metrics = model["predictive_metrics"]
    bootstrap = model["paired_bootstrap"]
    study_values = (
        (
            "eligible_ratings",
            "Eligible rating observations",
            split["total_ratings"],
            "ratings",
            "full eligible population",
            "fixed_study_result",
            "DuckDB",
            "#,0",
            "Eligible seller-associated ratings",
        ),
        (
            "historical_ratings",
            "Historical ratings",
            split["historical_ratings"],
            "ratings",
            "pre-cutoff population",
            "fixed_study_result",
            "DuckDB",
            "#,0",
            "Reviews before the locked cutoff",
        ),
        (
            "evaluation_ratings",
            "Evaluation ratings",
            split["evaluation_ratings"],
            "ratings",
            "post-cutoff population",
            "fixed_study_result",
            "DuckDB",
            "#,0",
            "Reviews on or after the cutoff",
        ),
        (
            "rated_sellers",
            "Rated sellers",
            len(all_source_ids),
            "sellers",
            "full eligible population",
            "fixed_study_result",
            "DuckDB",
            "#,0",
            "Distinct sellers with eligible ratings",
        ),
        (
            "evaluation_sellers",
            "Evaluation sellers",
            len(paired),
            "sellers",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "#,0",
            "Same seller universe for both methods",
        ),
        (
            "selected_prior_strength",
            "Selected prior strength",
            selected_m,
            "equivalent observations",
            "historical validation",
            "fixed_study_result",
            "prior_tuning.json",
            "0",
            "Selected using pre-2018 validation only",
        ),
        (
            "raw_mae",
            "Raw seller-equal MAE",
            metrics["raw"]["seller_equal_mae"],
            "rating stars",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.000",
            "Average seller-level absolute prediction error",
        ),
        (
            "adjusted_mae",
            "Adjusted seller-equal MAE",
            metrics["adjusted"]["seller_equal_mae"],
            "rating stars",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.000",
            "Average seller-level absolute prediction error",
        ),
        (
            "mae_improvement",
            "Absolute MAE improvement",
            metrics["mae_improvement_raw_minus_adjusted"],
            "rating stars",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.000",
            "Positive values favor adjustment",
        ),
        (
            "relative_mae_improvement",
            "Relative MAE improvement",
            metrics["relative_mae_improvement"],
            "proportion",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.0%",
            "Relative to raw seller-equal MAE",
        ),
        (
            "bootstrap_lower",
            "MAE improvement interval lower",
            bootstrap["confidence_interval_lower"],
            "rating stars",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.000",
            "Paired seller bootstrap lower bound",
        ),
        (
            "bootstrap_upper",
            "MAE improvement interval upper",
            bootstrap["confidence_interval_upper"],
            "rating stars",
            "paired evaluation population",
            "fixed_study_result",
            "model_comparison.json",
            "0.000",
            "Paired seller bootstrap upper bound",
        ),
    )
    tables["fact_study_summary"] = _table_rows(
        spec,
        "fact_study_summary",
        [
            dict(
                zip(
                    (
                        "metric_key",
                        "metric_label",
                        "metric_value",
                        "unit",
                        "population_label",
                        "classification",
                        "source_artifact",
                        "display_format",
                        "interpretation_note",
                    ),
                    values,
                    strict=True,
                )
            )
            for values in study_values
        ],
    )

    raw_metrics = error_metrics(
        paired.raw_estimates, paired.evaluation_means, paired.evaluation_counts
    )
    adjusted_metrics = error_metrics(
        paired.adjusted_estimates, paired.evaluation_means, paired.evaluation_counts
    )
    reconciliation = {
        "dataset": split
        | {"rated_sellers": len(all_source_ids), "historical_sellers": len(historical)},
        "evaluation": {
            "seller_count": len(paired),
            "raw_mae": raw_metrics.seller_equal_mae,
            "adjusted_mae": adjusted_metrics.seller_equal_mae,
            "mae_improvement": raw_metrics.seller_equal_mae
            - adjusted_metrics.seller_equal_mae,
        },
        "calibration": {"coverage": float(predictive.covered.mean())},
        "operational": {
            "orders": len(cohort),
            "sellers": len(operational_sellers),
            "evaluable": int(np.isfinite(cohort.delivered_late).sum()),
            "late": int(np.nansum(cohort.delivered_late)),
        },
        "key_version": key_version(secret),
        "unused_summary_loaded": bool(uncertainty),
        "population_checkpoint": population["future_operational_cohort"],
    }
    return tables, reconciliation


def _reconcile(
    tables: Mapping[str, Sequence[Mapping[str, object]]],
    reconciliation: Mapping[str, object],
    phase3b_dir: Path,
    phase3c_dir: Path,
) -> dict[str, object]:
    model = _json(phase3b_dir / "model_comparison.json")
    calibration = _json(phase3c_dir / "calibration_summary.json")
    population = _json(phase3c_dir / "population_and_missingness.json")[
        "future_operational_cohort"
    ]
    ranking = _json(phase3b_dir / "ranking_comparison.json")["method_comparison"]
    evaluation = reconciliation["evaluation"]
    assert_close(
        evaluation["raw_mae"],
        model["predictive_metrics"]["raw"]["seller_equal_mae"],
        name="raw MAE",
        tolerance=1e-12,
    )
    assert_close(
        evaluation["adjusted_mae"],
        model["predictive_metrics"]["adjusted"]["seller_equal_mae"],
        name="adjusted MAE",
        tolerance=1e-12,
    )
    assert_close(
        reconciliation["calibration"]["coverage"],
        calibration["primary"]["overall"]["empirical_coverage"],
        name="predictive coverage",
        tolerance=1e-12,
    )
    for key, source_key in (
        ("orders", "orders"),
        ("sellers", "sellers"),
        ("evaluable", "evaluable_lateness_orders"),
        ("late", "observed_late_orders"),
    ):
        if reconciliation["operational"][key] != population[source_key]:
            raise PowerBIValidationError(f"operational {key} does not reconcile")
    exported_evaluation = tables["fact_seller_evaluation"]
    raw_mae = float(np.mean([row["raw_absolute_error"] for row in exported_evaluation]))
    adjusted_mae = float(
        np.mean([row["adjusted_absolute_error"] for row in exported_evaluation])
    )
    assert_close(
        raw_mae, evaluation["raw_mae"], name="exported raw MAE", tolerance=1e-15
    )
    assert_close(
        adjusted_mae,
        evaluation["adjusted_mae"],
        name="exported adjusted MAE",
        tolerance=1e-15,
    )
    ranking_rows = tables["fact_ranking_summary"]
    assert_close(
        ranking_rows[0]["spearman"],
        ranking["spearman_correlation"],
        name="ranking Spearman",
        tolerance=1e-15,
    )
    return {
        "source_to_export": reconciliation,
        "export_recomputed": {"raw_mae": raw_mae, "adjusted_mae": adjusted_mae},
        "tolerances": {"statistical": 1e-12, "rounding_before_export": False},
        "power_bi_desktop_validated": False,
        "passed": True,
    }


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def _write_csv(
    path: Path, table: TableSpec, rows: Sequence[Mapping[str, object]]
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=table.columns, lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {column: _format_cell(row[column]) for column in table.columns}
            )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _fingerprints(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256_file(path)
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _publish(staging: Path, output: Path, *, overwrite: bool) -> None:
    if output.exists() and not overwrite:
        raise PowerBIValidationError(
            f"output directory already exists; use --overwrite: {output}"
        )
    backup = output.with_name(f".{output.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.replace(backup)
    try:
        staging.replace(output)
    except Exception:
        if backup.exists() and not output.exists():
            backup.replace(output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def run_export(
    *,
    database: Path,
    config: Path,
    export_spec_path: Path,
    phase3b_dir: Path,
    phase3c_dir: Path,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, object]:
    """Build, validate, stage, and atomically publish one complete export set."""

    _ensure_inputs(database, config, export_spec_path, phase3b_dir, phase3c_dir)
    spec = load_powerbi_export_spec(export_spec_path)
    protocol = load_statistical_protocol(config)
    if spec.primary_cutoff != protocol.primary_cutoff:
        raise PowerBIValidationError("Power BI and statistical cutoffs differ")
    secret = secret_from_environment()
    if output_dir.resolve() in {Path.cwd().resolve(), Path(output_dir.anchor)}:
        raise PowerBIValidationError("output directory is too broad")
    if output_dir.exists() and not overwrite:
        raise PowerBIValidationError(
            f"output directory already exists; use --overwrite: {output_dir}"
        )

    source_hashes = {
        "database": sha256_file(database),
        "statistical_protocol": sha256_file(config),
        "export_spec": sha256_file(export_spec_path),
        "phase3b": _fingerprints(phase3b_dir),
        "phase3c": _fingerprints(phase3c_dir),
    }
    connection = duckdb.connect(str(database), read_only=True)
    try:
        tables, reconciliation = build_export_tables(
            connection, spec, protocol, phase3b_dir, phase3c_dir, secret
        )
    finally:
        connection.close()

    schema_reports = [
        validate_table_rows(table, tables[table.name]) for table in spec.tables
    ]
    relationship_reports = validate_relationships(spec, tables)
    source_ids = aggregate_source_ids(database)
    privacy_report = validate_privacy(spec, tables, source_seller_ids=source_ids)
    statistical_report = _reconcile(tables, reconciliation, phase3b_dir, phase3c_dir)

    stage_parent = output_dir.parent
    stage_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=stage_parent)
    )
    try:
        exports = staging / "exports"
        validation = staging / "validation"
        metadata = staging / "metadata"
        exports.mkdir()
        validation.mkdir()
        metadata.mkdir()
        for table in spec.tables:
            _write_csv(exports / table.file, table, tables[table.name])
        _write_json(
            validation / "schema_validation.json",
            {"tables": schema_reports, "passed": True},
        )
        _write_json(
            validation / "relationship_validation.json",
            {"relationships": relationship_reports, "passed": True},
        )
        _write_json(validation / "statistical_reconciliation.json", statistical_report)
        _write_json(validation / "privacy_validation.json", privacy_report)
        exported_files = {
            table.file: {
                "table": table.name,
                "grain": table.grain,
                "rows": len(tables[table.name]),
                "columns": list(table.columns),
                "sha256": sha256_file(exports / table.file),
                "bytes": (exports / table.file).stat().st_size,
            }
            for table in spec.tables
        }
        manifest = {
            "pipeline_version": PIPELINE_VERSION,
            "spec_version": spec.spec_version,
            "exported_files": exported_files,
            "relationships": len(spec.relationships),
            "validation_passed": True,
            "contains_source_identifiers": False,
        }
        _write_json(metadata / "export_manifest.json", manifest)
        reproducibility = {
            "processing_timestamp_utc": datetime.now(UTC).isoformat(),
            "pipeline_version": PIPELINE_VERSION,
            "python_version": platform.python_version(),
            "duckdb_version": duckdb.__version__,
            "numpy_version": np.__version__,
            "primary_cutoff": protocol.primary_cutoff.isoformat(),
            "random_seed": protocol.random_seed,
            "posterior_draws": protocol.posterior_draws,
            "source_fingerprints": source_hashes,
            "pseudonym_key_version": reconciliation["key_version"],
            "secret_recorded": False,
        }
        _write_json(metadata / "reproducibility.json", reproducibility)
        (staging / "README_IMPORT.md").write_text(
            _local_import_readme(spec, manifest), encoding="utf-8"
        )
        _publish(staging, output_dir, overwrite=overwrite)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest


def aggregate_source_ids(database: Path) -> list[str]:
    """Read source seller IDs only for in-memory privacy validation."""

    connection = duckdb.connect(str(database), read_only=True)
    try:
        return [
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT seller_id
                FROM seller_rating_observations
                ORDER BY seller_id
                """
            ).fetchall()
        ]
    finally:
        connection.close()


def _local_import_readme(
    spec: PowerBIExportSpec, manifest: Mapping[str, object]
) -> str:
    files = "\n".join(
        f"- `exports/{table.file}` — {table.grain}." for table in spec.tables
    )
    relationship_lines = []
    for item in spec.relationships:
        child_side = "*" if item.cardinality == "many_to_one" else "1"
        arrow = "→" if item.cross_filter == "single" else "↔"
        relationship_lines.append(
            f"- `{item.to_table}[{item.to_column}]` (1) {arrow} "
            f"`{item.from_table}[{item.from_column}]` ({child_side}), "
            f"{item.cross_filter.capitalize()} cross-filter, active."
        )
    relationships = "\n".join(relationship_lines)
    return f"""# Local Power BI import package

This validated package contains {len(manifest["exported_files"])} UTF-8 CSV files. It is
real-data-derived, local-only, and does not establish publication permission.

## Files

{files}

## Relationships

{relationships}

Import every CSV with Power BI Desktop **Get data → Text/CSV → Transform data**.
Apply the types in `config/powerbi_export_spec.toml`, preserve blanks as nulls,
then create only the relationships above. Select the listed cardinality manually
if Power BI detects one-to-one on unique child keys. See
`docs/powerbi_import_guide.md` for the full Desktop steps.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--export-spec", type=Path, required=True)
    parser.add_argument("--phase3b-dir", type=Path, required=True)
    parser.add_argument("--phase3c-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = run_export(
            database=args.database,
            config=args.config,
            export_spec_path=args.export_spec,
            phase3b_dir=args.phase3b_dir,
            phase3c_dir=args.phase3c_dir,
            output_dir=args.output_dir,
            overwrite=args.overwrite,
        )
    except Exception as error:
        print(f"Power BI export failed: {error}", file=sys.stderr)
        return 1
    total_rows = sum(item["rows"] for item in manifest["exported_files"].values())
    print(
        f"Power BI export complete: {len(manifest['exported_files'])} files, "
        f"{total_rows} rows, all validation gates passed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
