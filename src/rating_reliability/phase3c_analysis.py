"""Run Phase 3C robustness, calibration, ranking, and operational research."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault(
    "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "rating-reliability-mpl")
)

import matplotlib.pyplot as plt  # noqa: E402

from rating_reliability.artifacts import sha256_file, write_json_artifact
from rating_reliability.calibration import (
    calibration_prior_sensitivity,
    primary_calibration_diagnostics,
    temporal_calibration,
    temporal_shift_diagnostics,
)
from rating_reliability.evaluation import (
    build_evaluation_data,
    error_metrics,
    tune_prior_strength,
)
from rating_reliability.historical import aggregate_sellers, pooled_reference
from rating_reliability.operational import (
    descriptive_associations,
    extract_operational_cohort,
    logistic_associations,
    operational_population_summary,
    rating_quartile_groups,
)
from rating_reliability.ranking import (
    average_ranks,
    pairwise_posterior_reliability,
    tie_aware_top_k,
    tie_diagnostics,
)
from rating_reliability.sensitivity import (
    count_threshold_sensitivity,
    cutoff_sensitivity,
    historical_variability_groups,
    prior_strength_sensitivity,
    reference_sensitivity,
)
from rating_reliability.statistical_analysis import (
    StatisticalAnalysisError,
    _publish_directory,
    _stage_directory,
    _validate_database,
    date_after_data,
)
from rating_reliability.statistical_config import load_statistical_protocol
from rating_reliability.uncertainty import posterior_alpha


class Phase3CError(Exception):
    """Expected Phase 3C failure suitable for concise CLI output."""


PHASE3B_FILES = (
    "model_comparison.json",
    "prior_tuning.json",
    "uncertainty_summary.json",
    "ranking_comparison.json",
    "evaluation_by_volume.csv",
    "reproducibility.json",
    "statistical_results.md",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Phase3CError(f"cannot read Phase 3B artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise Phase3CError(f"expected JSON object in {path}")
    return value


def _phase3b_inputs(directory: Path, protocol_hash: str) -> dict[str, Any]:
    missing = [name for name in PHASE3B_FILES if not (directory / name).is_file()]
    if missing:
        raise Phase3CError(f"Phase 3B directory is missing files: {', '.join(missing)}")
    result = {
        "model": _read_json(directory / "model_comparison.json"),
        "tuning": _read_json(directory / "prior_tuning.json"),
        "uncertainty": _read_json(directory / "uncertainty_summary.json"),
        "ranking": _read_json(directory / "ranking_comparison.json"),
        "reproducibility": _read_json(directory / "reproducibility.json"),
        "checksums": {name: sha256_file(directory / name) for name in PHASE3B_FILES},
    }
    if result["reproducibility"].get("protocol_sha256") != protocol_hash:
        raise Phase3CError("Phase 3B protocol checksum does not match locked config")
    return result


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def _flat_sensitivity(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for row in rows:
        raw = row.get("raw", {})
        adjusted = row.get("adjusted", {})
        bootstrap = row.get("paired_bootstrap", {})
        ranking = row.get("ranking_vs_raw", {})
        assert isinstance(raw, Mapping) and isinstance(adjusted, Mapping)
        assert isinstance(bootstrap, Mapping)
        assert isinstance(ranking, Mapping)
        result.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"raw", "adjusted", "paired_bootstrap", "ranking_vs_raw"}
            }
            | {
                "raw_mae": raw.get("seller_equal_mae"),
                "adjusted_mae": adjusted.get("seller_equal_mae"),
                "raw_rmse": raw.get("seller_equal_rmse"),
                "adjusted_rmse": adjusted.get("seller_equal_rmse"),
                "raw_review_weighted_mae": raw.get("review_weighted_mae"),
                "adjusted_review_weighted_mae": adjusted.get("review_weighted_mae"),
                "bootstrap_lower": bootstrap.get("confidence_interval_lower"),
                "bootstrap_upper": bootstrap.get("confidence_interval_upper"),
                "ranking_spearman": ranking.get("spearman_correlation"),
                "ranking_kendall_tau_b": ranking.get("kendall_tau_b"),
            }
        )
    return result


def _operational_csv_rows(
    associations: list[dict[str, object]],
    quartiles: list[dict[str, object]],
    logistic: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows = associations + quartiles
    for model in logistic:
        for coefficient in model["coefficients"]:
            rows.append(
                {
                    "analysis": model["analysis"],
                    "method": model["method"],
                    "model": model["model"],
                    "classification": model["classification"],
                    "order_count": model["order_count"],
                    "seller_count": model["seller_clusters"],
                    "term": coefficient["term"],
                    "estimate": coefficient["log_odds"],
                    "standard_error": coefficient["cluster_standard_error"],
                    "odds_ratio": coefficient["odds_ratio"],
                    "ci_lower": coefficient["odds_ratio_ci_lower"],
                    "ci_upper": coefficient["odds_ratio_ci_upper"],
                    "context_note": model["context_note"],
                }
            )
    return rows


def _style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "figure.figsize": (8, 5),
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "font.size": 9,
        }
    )


def _save(path: Path) -> None:
    plt.tight_layout()
    plt.savefig(path, dpi=160, bbox_inches="tight", metadata={"Software": "Phase3C"})
    plt.close()


def _plots(
    directory: Path,
    *,
    prior_rows: list[dict[str, object]],
    count_rows: list[dict[str, object]],
    cutoff_rows: list[dict[str, object]],
    variability_rows: list[dict[str, object]],
    paired: Any,
    tie_rows: list[dict[str, object]],
    calibration_rows: list[dict[str, object]],
    operational_rows: list[dict[str, object]],
    population: Mapping[str, Any],
) -> list[str]:
    directory.mkdir(parents=True, exist_ok=False)
    _style()
    names: list[str] = []

    x = [row["prior_strength"] for row in prior_rows]
    y = [row["mae_improvement"] for row in prior_rows]
    plt.plot(x, y, marker="o", color="#285f8f")
    plt.xscale("log")
    plt.axhline(0, color="black", linewidth=0.8)
    plt.xlabel("Prior strength m (log scale)")
    plt.ylabel("MAE improvement (raw − adjusted)")
    plt.title("Final-period MAE sensitivity across prespecified prior strengths")
    names.append("prior_strength_sensitivity.png")
    _save(directory / names[-1])

    for prefix, title in (
        ("historical_min_", "Historical review-count threshold sensitivity"),
        ("evaluation_min_", "Future review-count threshold sensitivity"),
    ):
        selected = [row for row in count_rows if row["scenario_id"].startswith(prefix)]
        thresholds = [
            row["historical_minimum"]
            if prefix.startswith("historical")
            else row["evaluation_minimum"]
            for row in selected
        ]
        plt.plot(
            thresholds,
            [row["mae_improvement"] for row in selected],
            marker="o",
            color="#3a7d44",
        )
        plt.axhline(0, color="black", linewidth=0.8)
        plt.xlabel("Minimum rating count")
        plt.ylabel("MAE improvement (raw − adjusted)")
        plt.title(title)
        names.append(f"{prefix.rstrip('_')}_sensitivity.png")
        _save(directory / names[-1])

    summaries = [row for row in cutoff_rows if row["is_prespecified_summary"]]
    plt.bar(
        [row["cutoff"] for row in summaries],
        [row["mae_improvement"] for row in summaries],
        color="#7a5195",
    )
    plt.axhline(0, color="black", linewidth=0.8)
    plt.ylabel("MAE improvement at prespecified m=10")
    plt.title("Alternative-cutoff robustness (populations and horizons differ)")
    plt.xticks(rotation=20)
    names.append("alternative_cutoff_sensitivity.png")
    _save(directory / names[-1])

    plt.bar(
        [row["scenario_id"].replace("variability_", "") for row in variability_rows],
        [row["mae_improvement"] for row in variability_rows],
        color="#ef8354",
    )
    plt.axhline(0, color="black", linewidth=0.8)
    plt.ylabel("MAE improvement")
    plt.title("Adjustment benefit by historical rating variability")
    plt.xticks(rotation=20, ha="right")
    names.append("historical_variability.png")
    _save(directory / names[-1])

    bins = np.linspace(1, 5, 30)
    plt.hist(paired.raw_estimates, bins=bins, alpha=0.55, label="Raw", density=True)
    plt.hist(
        paired.adjusted_estimates,
        bins=bins,
        alpha=0.55,
        label="Adjusted",
        density=True,
    )
    plt.xlabel("Historical rating estimate")
    plt.ylabel("Seller density")
    plt.title(
        f"Raw and adjusted historical rating distributions (n={len(paired):,} sellers)"
    )
    plt.legend()
    names.append("raw_adjusted_distributions.png")
    _save(directory / names[-1])

    ks = [row["k"] for row in tie_rows]
    minimum = [row["minimum_possible_overlap_proportion"] for row in tie_rows]
    maximum = [row["maximum_possible_overlap_proportion"] for row in tie_rows]
    fractional = [row["fractional_expected_overlap_proportion"] for row in tie_rows]
    plt.vlines(ks, minimum, maximum, color="#444444", linewidth=5, alpha=0.6)
    plt.scatter(ks, fractional, color="#d62728", label="Fractional expectation")
    plt.xlabel("K")
    plt.ylabel("Raw/adjusted top-K overlap proportion")
    plt.ylim(0, 1)
    plt.title("Tie-aware range of top-K overlap")
    plt.legend()
    names.append("tie_aware_top_k.png")
    _save(directory / names[-1])

    rank_change = np.abs(
        average_ranks(paired.raw_estimates) - average_ranks(paired.adjusted_estimates)
    )
    plt.scatter(
        paired.historical_counts,
        rank_change,
        s=8,
        alpha=0.35,
        color="#285f8f",
    )
    plt.xscale("log")
    plt.xlabel("Historical rating count (log scale)")
    plt.ylabel("Absolute average-rank change")
    plt.title("Ranking movement is concentrated among lower-volume sellers")
    names.append("ranking_movement_by_volume.png")
    _save(directory / names[-1])

    historical_cal = [
        row for row in calibration_rows if row["dimension"] == "historical_volume"
    ]
    plt.plot(
        [row["group"] for row in historical_cal],
        [row["coverage"] for row in historical_cal],
        marker="o",
        label="Observed",
    )
    plt.axhline(0.95, color="black", linestyle="--", label="Nominal 95%")
    plt.ylim(0.75, 1.0)
    plt.ylabel("Predictive coverage")
    plt.xlabel("Historical rating-count group")
    plt.title("Predictive coverage declines for high-history sellers")
    plt.legend()
    names.append("calibration_coverage_by_volume.png")
    _save(directory / names[-1])

    plt.plot(
        [row["group"] for row in historical_cal],
        [row["mean_width"] for row in historical_cal],
        marker="o",
        color="#ef8354",
    )
    plt.ylabel("Mean predictive interval width (stars)")
    plt.xlabel("Historical rating-count group")
    plt.title("Predictive intervals narrow as historical evidence increases")
    names.append("calibration_width_by_volume.png")
    _save(directory / names[-1])

    adjusted_quartiles = [
        row
        for row in operational_rows
        if row.get("analysis") == "seller_rating_quartile"
        and row.get("method") == "adjusted"
    ]
    plt.bar(
        [row["group"] for row in adjusted_quartiles],
        [row["late_rate"] for row in adjusted_quartiles],
        color="#3a7d44",
    )
    plt.ylabel("Late-delivery rate")
    plt.xlabel("Historical adjusted-rating quartile")
    plt.title("Later delivery lateness by adjusted-rating quartile")
    names.append("rating_delivery_association.png")
    _save(directory / names[-1])

    statuses = population["future_operational_cohort"]["status_distribution"]
    plt.bar(statuses.keys(), statuses.values(), color="#7a5195")
    plt.ylabel("Orders")
    plt.xlabel("Source order status")
    plt.title("Future operational cohort fulfillment outcomes")
    plt.xticks(rotation=20)
    names.append("delivery_outcome_availability.png")
    _save(directory / names[-1])
    return names


def _research_report(
    *,
    phase3b: Mapping[str, Any],
    robustness: Mapping[str, Any],
    ranking: Mapping[str, Any],
    calibration: Mapping[str, Any],
    operational: Mapping[str, Any],
    population: Mapping[str, Any],
    figures: list[str],
) -> str:
    model = phase3b["model"]
    primary = model["predictive_metrics"]
    rating_population = phase3b["reproducibility"]["population_reconciliation"]
    prior_strength = model["selected_prior_strength"]
    prior = robustness["prior_strength"]
    reference = robustness["reference"]
    count = robustness["count_thresholds"]
    cutoff = [row for row in robustness["cutoffs"] if row["is_prespecified_summary"]]
    variability = robustness["variability"]
    op_corr = operational["correlations"]
    raw_late = next(
        row
        for row in op_corr
        if row["method"] == "raw" and row["outcome"] == "late_delivery_rate"
    )
    adjusted_late = next(
        row
        for row in op_corr
        if row["method"] == "adjusted" and row["outcome"] == "late_delivery_rate"
    )
    future = population["future_operational_cohort"]
    all_prior_positive = all(row["mae_improvement"] > 0 for row in prior)
    all_cutoff_positive = all(row["mae_improvement"] > 0 for row in cutoff)
    historical_thresholds = [
        row for row in count if row["scenario_id"].startswith("historical_min_")
    ]
    figure_lines = "\n".join(f"- `figures/{name}`" for name in figures)
    return f"""# Phase 3C Research Findings

## 1. Executive summary

Under the locked 2018 temporal evaluation, adjusted historical estimates retained
the Phase 3B seller-equal MAE advantage: {primary["raw"]["seller_equal_mae"]:.6f}
for raw means versus {primary["adjusted"]["seller_equal_mae"]:.6f} for adjustment,
an improvement of {primary["mae_improvement_raw_minus_adjusted"]:.6f}. The primary
experiment was not rerun or overwritten by sensitivity selection. Adjustment
remained favorable across all predefined prior strengths:
{str(all_prior_positive).lower()}.
All prespecified alternative-cutoff summary comparisons favored adjustment:
{str(all_cutoff_positive).lower()}, although their populations and horizons differ.

Exact top-K membership was not a defensible standalone conclusion because raw
ratings contain large boundary ties. Tie-aware bounds and fractional membership
show how much of the reported zero overlap is a boundary artifact. Predictive
interval coverage remained below the nominal 95% overall and was weakest among
high-history sellers; descriptive temporal shifts and narrower intervals are
consistent with, but do not identify, possible causes.

Historical ratings were associated with later delivery lateness, but this is
observational. The adjusted-rating Spearman association with seller late rate was
{adjusted_late["spearman"]:.4f}, compared with {raw_late["spearman"]:.4f} for raw
ratings, over the same {adjusted_late["seller_count"]:,} sellers and
{adjusted_late["order_count"]:,} evaluable orders.

## 2. Research questions

The primary question asks whether adjusted customer order-experience ratings
provide more reliable seller comparisons than arithmetic means when evidence
volumes differ. The secondary question asks how historical rating estimates are
associated with later operational outcomes. Neither question treats ratings as
objective seller quality or operational associations as causal effects.

## 3. Dataset and analytical population

Phase 2C contains {rating_population["total_ratings"]:,} eligible seller-associated
order ratings for {rating_population["rated_sellers"]:,} sellers. The locked split
contains {rating_population["historical_ratings"]:,} historical and
{rating_population["evaluation_ratings"]:,} evaluation ratings. Phase 3B compares
{primary["seller_count"]:,} sellers in both periods. The future operational
cohort requires purchase and review dates on/after {model["cutoff"]} and a historical
seller estimate: {future["orders"]:,} orders from {future["sellers"]:,} sellers.

## 4. Locked Phase 3B finding

The primary model uses the review-pooled historical reference and historically
tuned m={prior_strength}. Raw MAE was {primary["raw"]["seller_equal_mae"]:.6f}.
Adjusted MAE was {primary["adjusted"]["seller_equal_mae"]:.6f}.
Relative improvement was
{primary["relative_mae_improvement"]:.2%}. The paired seller bootstrap interval
for improvement remains
[{phase3b["model"]["paired_bootstrap"]["confidence_interval_lower"]:.6f},
{phase3b["model"]["paired_bootstrap"]["confidence_interval_upper"]:.6f}].

## 5. Robustness classification

The primary analysis retains its locked design with a rating availability
amendment. Prior, reference, configured count thresholds, and cutoff checks
are preplanned sensitivities. Historical minimum 50 and future minima 3 and 20
are supplemental. Historical variability, calibration
diagnostics, pair sampling, and operational models are exploratory diagnostics.
The historical tuning choice stays fixed at m={prior_strength}.

## 6. Prior-strength sensitivity

The final-period curve reports every predefined m without retuning. Improvement
ranged from {min(row["mae_improvement"] for row in prior):.6f} to
{max(row["mae_improvement"] for row in prior):.6f}; all values favored adjustment.
This robustness does not validate selecting the best final-period value.

## 7. Reference-weighting sensitivity

The pooled reference mean was {reference[0]["reference_mean"]:.6f}; the
seller-equal categorical reference mean was {reference[1]["reference_mean"]:.6f}.
With fixed m={prior_strength}, MAE improvement was
{reference[0]["mae_improvement"]:.6f} and
{reference[1]["mae_improvement"]:.6f}, respectively. The primary reference is
unchanged.

## 8. Review-count sensitivity

Historical-threshold improvement decreased from
{historical_thresholds[0]["mae_improvement"]:.6f} at n>=1 to
{historical_thresholds[-1]["mae_improvement"]:.6f} at n>=50. This supports the
predefined expectation that shrinkage helps most when history is limited. Future
count restrictions also retained the direction but are retrospective cohorts,
not deployable eligibility rules, and may select persistent sellers.

## 9. Alternative cutoffs

Phase 3A requires a complete fixed-m grid and prespecified m=10 summary for
alternative cutoffs; it does not permit using their later outcomes to choose m.
The prompt's suggestion to retune was therefore superseded by the locked plan.
Summary improvements were:
{", ".join(f"{row['cutoff']}: {row['mae_improvement']:.6f}" for row in cutoff)}.
These are not directly comparable because evaluation horizons and seller sets vary.

## 10. Historical variability

One-review sellers retain undefined sample variance and are reported separately.
Zero-variance and positive-variance sellers are also separated, with positive
variance divided at its historical-only median. Improvements were
{
        ", ".join(
            f"{row['scenario_id'].replace('variability_', '')}: "
            f"{row['mae_improvement']:.6f}"
            for row in variability
        )
    }.
Variability is confounded with sample size, so these results are diagnostic.

## 11. Tie-aware ranking findings

There were {ranking["tie_distribution"]["distinct_raw_values"]:,} distinct raw
values; {ranking["tie_distribution"]["sellers_in_ties"]:,} sellers belonged to
ties, and the largest tie contained {ranking["tie_distribution"]["largest_tie_group"]:,}
sellers. Spearman uses average ranks and Kendall tau-b handles ties. Exact top-K
membership uses seller ID only for reproducibility and is not substantive.

Tie-aware overlap bounds are reported for K=10, 25, and 50. Fractional overlap
assumes uniform random ordering within the raw boundary tie. Zero deterministic
overlap therefore does not mean the methods identify disjoint sets of truly best
sellers.

## 12. Ranking uncertainty

The sampled-pair analysis covers {ranking["pairwise_reliability"]["sampled_pairs"]:,}
of {ranking["pairwise_reliability"]["total_possible_pairs"]:,} possible seller
pairs using {ranking["pairwise_reliability"]["posterior_draws"]:,} posterior draws.
The share with at least 90% ordering support was
{ranking["pairwise_reliability"]["strong_support_at_least_0_90"]:.2%}. These are
model-conditional expected-rating comparisons, not objective superiority.

## 13. Posterior predictive calibration

Overall coverage was {calibration["primary"]["overall"]["empirical_coverage"]:.2%}
at a nominal {calibration["primary"]["nominal_coverage"]:.0%}, with mean width
{calibration["primary"]["overall"]["mean_width"]:.4f}. Misses were below/inside/
above = {calibration["primary"]["miss_direction"]["below"]}/
{calibration["primary"]["miss_direction"]["inside"]}/
{calibration["primary"]["miss_direction"]["above"]}. Coverage, width, evaluation
time, historical variability, and prior-strength diagnostics are all reported.

The marketplace and paired-seller shifts are descriptive. Temporal drift,
within-seller dependence, selection, plug-in prior uncertainty, and model
misspecification remain competing explanations; none is identified causally.

## 14. Operational associations

Lateness is defined only for delivered orders with valid estimated and actual
timestamps. Of {future["orders"]:,} future-cohort orders, {future["delivered_orders"]:,}
were marked delivered and {future["evaluable_lateness_orders"]:,} had evaluable
lateness. The observed late rate was {future["observed_late_rate"]:.2%}. Cancelled
and other non-delivered statuses are not classified as on-time deliveries.

Seller-level correlations, rating quartiles, and seller-clustered order-level
logistic models compare raw and adjusted ratings on identical outcome samples.
Contextual order-value and freight controls are explicitly future-order context,
not cutoff-time seller predictors. No coefficient is a causal effect.

## 15. Selection and missingness

Only reviewed, unambiguous, single-seller orders enter rating analysis. Historical
and future seller survival is selective. Product category was missing for
{future["category_missing_orders"]:,} operational-cohort orders. Only
{future["unresolved_delivered_orders"]:,} delivered orders had unresolved
lateness timestamps; assumption-dependent late-rate bounds are reported without
forcing cancelled orders into either bound.

## 16. Business implications

- Display rating evidence volume beside any point estimate.
- For low-volume sellers, consider an adjusted estimate and uncertainty rather
  than presenting the raw average with equal apparent precision.
- Avoid exact top-K claims when boundary ties or posterior rank uncertainty are
  substantial; show tie-aware membership or score bands.
- Present delivery and fulfillment measures as complementary operational context,
  not as proof of what caused a customer rating.
- Monitor temporal calibration before any deployment and avoid assuming a model
  remains calibrated merely because its point predictions improve.

## 17. Recommendations

The evidence supports using shrinkage as a comparison aid under the studied
temporal design, especially for low-history sellers. It does not support hiding
raw counts, replacing uncertainty with a single rank, or applying these findings
unchanged to another marketplace. A production proposal would require current
data, prospective monitoring, stakeholder review, and a resolved publication and
usage basis.

## 18. Limitations

Reviews are selected observations; order ratings mix product, logistics,
marketplace, seller, and expectation effects. Multi-seller exclusions,
survivorship, changing composition, temporal drift, clustered orders, missing
outcomes, and empirical-Bayes plug-in assumptions limit generalization. The
operational analysis is noncausal and exploratory, with multiple comparisons.

## 19. Reproducibility

The Phase 3C CLI records database, protocol, and all Phase 3B checksums; seeds,
draw counts, dependency versions, scenarios, and chart files. Aggregate outputs
are staged before replacement. Core analytical artifacts are deterministic for
unchanged inputs; processing timestamps may differ.

Generated figures:

{figure_lines}

## 20. Publication restrictions

Olist's published CC BY-NC-SA 4.0 conditions require attribution,
noncommercial use, and share-alike treatment where applicable. Underlying rights
and portfolio-use implications remain unresolved. This report, figures, and all
real-data-derived outputs remain local and Git-ignored.
"""


def run_phase3c(
    *,
    database: Path,
    config: Path,
    phase3b_directory: Path,
    output_directory: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run Phase 3C and atomically publish local aggregate research outputs."""

    if not database.is_file():
        raise Phase3CError(f"database does not exist: {database}")
    if output_directory.exists() and not overwrite:
        raise Phase3CError(
            f"output exists; use --overwrite to replace: {output_directory}"
        )
    protocol = load_statistical_protocol(config)
    protocol_hash = sha256_file(config)
    phase3b = _phase3b_inputs(phase3b_directory, protocol_hash)
    before_phase3b = dict(phase3b["checksums"])
    staged = _stage_directory(output_directory)
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(database), read_only=True)
        population = _validate_database(connection, protocol)
        tuning = tune_prior_strength(connection, protocol)
        if (
            tuning["selected_prior_strength"]
            != phase3b["tuning"]["selected_prior_strength"]
        ):
            raise Phase3CError("rerun tuning does not reproduce Phase 3B selected m")
        selected_m = float(tuning["selected_prior_strength"])
        end = date_after_data(connection)
        historical = aggregate_sellers(
            connection, end_exclusive=protocol.primary_cutoff
        )
        evaluation = aggregate_sellers(
            connection, start_inclusive=protocol.primary_cutoff, end_exclusive=end
        )
        reference = pooled_reference(connection, end_exclusive=protocol.primary_cutoff)
        paired = build_evaluation_data(
            historical,
            evaluation,
            reference_mean=reference.mean_rating,
            prior_strength=selected_m,
        )
        checkpoint = phase3b["model"]["predictive_metrics"]
        if len(paired) != checkpoint["seller_count"]:
            raise Phase3CError("Phase 3B evaluation population was not reproduced")
        reproduced_raw = error_metrics(
            paired.raw_estimates,
            paired.evaluation_means,
            paired.evaluation_counts,
        )
        reproduced_adjusted = error_metrics(
            paired.adjusted_estimates,
            paired.evaluation_means,
            paired.evaluation_counts,
        )
        if not np.isclose(
            reproduced_raw.seller_equal_mae,
            checkpoint["raw"]["seller_equal_mae"],
            rtol=0,
            atol=1e-12,
        ) or not np.isclose(
            reproduced_adjusted.seller_equal_mae,
            checkpoint["adjusted"]["seller_equal_mae"],
            rtol=0,
            atol=1e-12,
        ):
            raise Phase3CError("Phase 3B primary MAE values were not reproduced")

        prior_rows = prior_strength_sensitivity(
            paired,
            reference_mean=reference.mean_rating,
            cutoff=protocol.primary_cutoff,
            protocol=protocol,
        )
        reference_rows = reference_sensitivity(
            paired,
            historical,
            pooled_mean=reference.mean_rating,
            selected_m=selected_m,
            cutoff=protocol.primary_cutoff,
            top_k=protocol.ranking_top_k,
        )
        count_rows = count_threshold_sensitivity(
            paired,
            reference_mean=reference.mean_rating,
            selected_m=selected_m,
            cutoff=protocol.primary_cutoff,
            protocol=protocol,
        )
        alternative_cutoffs = tuple(
            date.fromisoformat(value)
            for value in ("2017-07-01", "2018-02-03", "2018-04-01")
        )
        cutoff_rows = cutoff_sensitivity(
            connection,
            cutoffs=alternative_cutoffs,
            final_end=end,
            protocol=protocol,
        )
        variability_rows, variability_labels = historical_variability_groups(
            paired,
            reference_mean=reference.mean_rating,
            selected_m=selected_m,
            cutoff=protocol.primary_cutoff,
            protocol=protocol,
        )
        robustness = {
            "classification": {
                "primary": "locked_phase3b",
                "prior_reference_configured_counts_cutoffs": "preplanned_sensitivity",
                "additional_count_thresholds": "supplemental_sensitivity",
                "variability": "post_hoc_exploratory",
            },
            "protocol_conflict_resolution": (
                "Alternative cutoffs use the Phase 3A fixed grid and m=10 "
                "summary, not prompt-suggested retuning."
            ),
            "prior_strength": prior_rows,
            "reference": reference_rows,
            "count_thresholds": count_rows,
            "cutoffs": cutoff_rows,
            "variability": variability_rows,
        }

        tie_distribution = tie_diagnostics(
            paired.raw_estimates, paired.historical_counts
        )
        tie_rows = [
            tie_aware_top_k(
                paired.raw_estimates,
                paired.adjusted_estimates,
                paired.seller_ids,
                value,
            )
            for value in protocol.ranking_top_k
        ]
        alphas = posterior_alpha(
            paired.historical_category_counts,
            reference.probabilities,
            selected_m,
        )
        pairwise = pairwise_posterior_reliability(
            alphas,
            paired.historical_counts,
            paired.raw_estimates,
            paired.adjusted_estimates,
            pair_sample=50_000,
            draws=5_000,
            seed=protocol.random_seed + 30,
        )
        ranking = {
            "phase3b_method_comparison": phase3b["ranking"]["method_comparison"],
            "phase3b_posterior_stability": phase3b["ranking"]["posterior_stability"],
            "tie_distribution": tie_distribution,
            "tie_aware_top_k": tie_rows,
            "pairwise_reliability": pairwise,
        }

        calibration_primary, calibration_rows = primary_calibration_diagnostics(
            paired,
            reference.probabilities,
            prior_strength=selected_m,
            draws=protocol.posterior_draws,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed + 1,
            variability_labels=variability_labels,
        )
        calibration_prior = calibration_prior_sensitivity(
            paired,
            reference.probabilities,
            prior_grid=protocol.prior_strength_grid,
            draws=5_000,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed + 40,
        )
        calibration_time = temporal_calibration(
            connection,
            paired,
            reference.probabilities,
            prior_strength=selected_m,
            draws=5_000,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed + 50,
        )
        calibration_rows.extend(calibration_prior)
        calibration_rows.extend(calibration_time)
        calibration = {
            "primary": calibration_primary,
            "temporal_shift": temporal_shift_diagnostics(
                connection, paired, cutoff=protocol.primary_cutoff, end=end
            ),
            "prior_sensitivity_draws": 5_000,
            "time_diagnostic_draws": 5_000,
            "limitations": [
                "coverage is conditional on realized future sample size",
                "coverage is not latent expected-rating confidence coverage",
                "wider intervals can improve coverage without improving sharpness",
                "temporal shift diagnostics do not identify causes",
            ],
        }

        operational_cohort = extract_operational_cohort(
            connection,
            historical,
            cutoff=protocol.primary_cutoff,
            reference_mean=reference.mean_rating,
            prior_strength=selected_m,
        )
        op_associations, seller_ops = descriptive_associations(
            operational_cohort,
            resamples=protocol.bootstrap_resamples,
            seed=protocol.random_seed,
        )
        quartiles = rating_quartile_groups(seller_ops, "raw")
        quartiles.extend(rating_quartile_groups(seller_ops, "adjusted"))
        logistic = logistic_associations(operational_cohort)
        population_missingness = operational_population_summary(
            connection,
            operational_cohort,
            cutoff=protocol.primary_cutoff,
            historical_sellers=len(historical),
            evaluation_sellers=len(evaluation),
            paired_sellers=len(paired),
        )
        operational = {
            "classification": "exploratory_noncausal",
            "correlations": op_associations,
            "rating_quartiles": quartiles,
            "logistic_models": logistic,
            "fairness": (
                "Raw and adjusted predictors use identical seller/order outcome "
                "samples, missingness rules, and controls."
            ),
        }
        operational_csv = _operational_csv_rows(op_associations, quartiles, logistic)

        staged.mkdir(parents=True, exist_ok=False)
        figures = _plots(
            staged / "figures",
            prior_rows=prior_rows,
            count_rows=count_rows,
            cutoff_rows=cutoff_rows,
            variability_rows=variability_rows,
            paired=paired,
            tie_rows=tie_rows,
            calibration_rows=calibration_rows,
            operational_rows=operational_csv,
            population=population_missingness,
        )
        write_json_artifact(robustness, staged / "robustness_summary.json")
        _write_rows(staged / "prior_sensitivity.csv", _flat_sensitivity(prior_rows))
        _write_rows(
            staged / "reference_sensitivity.csv", _flat_sensitivity(reference_rows)
        )
        _write_rows(
            staged / "count_threshold_sensitivity.csv",
            _flat_sensitivity(count_rows),
        )
        _write_rows(staged / "cutoff_sensitivity.csv", _flat_sensitivity(cutoff_rows))
        _write_rows(
            staged / "variability_sensitivity.csv",
            _flat_sensitivity(variability_rows),
        )
        write_json_artifact(
            {
                "tie_distribution": tie_distribution,
                "tie_aware_top_k": tie_rows,
                "tie_interpretation": (
                    "Exact deterministic membership is reproducible but arbitrary "
                    "inside raw-score boundary ties."
                ),
            },
            staged / "ranking_tie_analysis.json",
        )
        write_json_artifact(ranking, staged / "ranking_stability_summary.json")
        _write_rows(staged / "calibration_diagnostics.csv", calibration_rows)
        write_json_artifact(calibration, staged / "calibration_summary.json")
        _write_rows(staged / "operational_associations.csv", operational_csv)
        write_json_artifact(operational, staged / "operational_summary.json")
        write_json_artifact(
            population_missingness, staged / "population_and_missingness.json"
        )
        report = _research_report(
            phase3b=phase3b,
            robustness=robustness,
            ranking=ranking,
            calibration=calibration,
            operational=operational,
            population=population_missingness,
            figures=figures,
        )
        (staged / "research_findings.md").write_text(
            report, encoding="utf-8", newline="\n"
        )
        after_phase3b = {
            name: sha256_file(phase3b_directory / name) for name in PHASE3B_FILES
        }
        if before_phase3b != after_phase3b:
            raise Phase3CError("Phase 3B artifacts changed during Phase 3C")
        reproducibility = {
            "phase": "3C",
            "processing_timestamp_utc": datetime.now(UTC).isoformat(),
            "database_file_name": database.name,
            "database_sha256": sha256_file(database),
            "protocol_sha256": protocol_hash,
            "phase3b_checksums": before_phase3b,
            "primary_cutoff": protocol.primary_cutoff.isoformat(),
            "selected_primary_prior_strength": selected_m,
            "bootstrap_resamples": protocol.bootstrap_resamples,
            "posterior_draws_primary": protocol.posterior_draws,
            "random_seed": protocol.random_seed,
            "sensitivity_settings": {
                "historical_minimums": [1, 5, 10, 20, 50],
                "evaluation_minimums": [1, 3, 5, 10, 20],
                "alternative_cutoffs": [
                    value.isoformat() for value in alternative_cutoffs
                ],
                "alternative_cutoff_summary_m": 10,
                "pair_sample": 50_000,
                "pair_draws": 5_000,
                "calibration_sensitivity_draws": 5_000,
            },
            "software": {
                "python": sys.version.split()[0],
                "duckdb": duckdb.__version__,
                "numpy": np.__version__,
                "matplotlib": __import__("matplotlib").__version__,
            },
            "population_reconciliation": population,
            "figures": figures,
            "privacy": {
                "seller_level_exports": False,
                "review_text_exported": False,
                "customer_identifiers_exported": False,
            },
        }
        write_json_artifact(reproducibility, staged / "reproducibility.json")
        _publish_directory(staged, output_directory, overwrite=overwrite)
        return {
            "robustness": robustness,
            "ranking": ranking,
            "calibration": calibration,
            "operational": operational,
            "population": population_missingness,
            "reproducibility": reproducibility,
        }
    except Phase3CError:
        raise
    except (StatisticalAnalysisError, duckdb.Error, OSError, ValueError) as error:
        raise Phase3CError(f"Phase 3C analysis failed: {error}") from error
    finally:
        if connection is not None:
            connection.close()
        if staged.exists():
            shutil.rmtree(staged)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete Phase 3C research analysis."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--config", type=Path, default=Path("config/statistical_protocol.toml")
    )
    parser.add_argument(
        "--phase3b-dir",
        type=Path,
        default=Path("reports/local/statistical_analysis"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/local/phase3c")
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_phase3c(
            database=args.database,
            config=args.config,
            phase3b_directory=args.phase3b_dir,
            output_directory=args.output_dir,
            overwrite=args.overwrite,
        )
    except Phase3CError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    primary = result["calibration"]["primary"]["overall"]
    future = result["population"]["future_operational_cohort"]
    print("Phase 3C complete")
    print(f"predictive coverage: {primary['empirical_coverage']:.4%}")
    print(f"future operational orders: {future['orders']}")
    print(f"observed late-delivery rate: {future['observed_late_rate']:.4%}")
    print(f"outputs: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
