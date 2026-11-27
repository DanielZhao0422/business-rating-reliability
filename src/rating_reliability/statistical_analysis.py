"""Run the locked Phase 3B historical statistical evaluation."""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import numpy as np

from rating_reliability.artifacts import sha256_file, write_json_artifact
from rating_reliability.evaluation import (
    EvaluationData,
    build_evaluation_data,
    error_metrics,
    metric_dict,
    metrics_by_group,
    paired_mae_bootstrap,
    tune_prior_strength,
    volume_group,
)
from rating_reliability.historical import (
    StatisticalDataError,
    aggregate_sellers,
    assert_window_integrity,
    pooled_reference,
)
from rating_reliability.olist_prepare import RATING_AVAILABILITY_FIELD
from rating_reliability.ranking import (
    posterior_ranking_stability,
    ranking_comparison,
)
from rating_reliability.statistical_config import (
    StatisticalConfigError,
    StatisticalProtocol,
    load_statistical_protocol,
)
from rating_reliability.uncertainty import (
    credible_intervals,
    posterior_alpha,
    predictive_intervals,
    summarize_intervals,
)


class StatisticalAnalysisError(Exception):
    """An expected analysis failure suitable for concise CLI display."""


REQUIRED_TABLES = frozenset(
    {
        "seller_rating_observations",
        "historical_rating_observations",
        "evaluation_rating_observations",
        "pipeline_metadata",
        "analytical_quality_checks",
    }
)
OUTPUT_NAMES = (
    "model_comparison.json",
    "prior_tuning.json",
    "uncertainty_summary.json",
    "ranking_comparison.json",
    "evaluation_by_volume.csv",
    "reproducibility.json",
    "statistical_results.md",
)


def _application_version() -> str:
    try:
        return version("rating-reliability")
    except PackageNotFoundError:
        return "unknown"


def _validate_database(
    connection: duckdb.DuckDBPyConnection, protocol: StatisticalProtocol
) -> dict[str, object]:
    tables = {str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()}
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise StatisticalAnalysisError(
            f"analytical database is missing required tables: {', '.join(missing)}"
        )
    failures = connection.execute(
        "SELECT check_name, failure_count FROM analytical_quality_checks "
        "WHERE failure_count <> 0 ORDER BY check_name"
    ).fetchall()
    if failures:
        detail = ", ".join(f"{name}={count}" for name, count in failures)
        raise StatisticalAnalysisError(f"analytical quality checks failed: {detail}")
    metadata = {
        str(key): str(value)
        for key, value in connection.execute(
            "SELECT metadata_key, metadata_value FROM pipeline_metadata"
        ).fetchall()
    }
    expected_cutoff = protocol.primary_cutoff.isoformat()
    if metadata.get("rating_availability_field") != RATING_AVAILABILITY_FIELD:
        raise StatisticalAnalysisError(
            "database does not use review answer timestamps for rating availability; "
            "rebuild it with olist_prepare"
        )
    if metadata.get("historical_cutoff") != expected_cutoff:
        raise StatisticalAnalysisError(
            "database cutoff does not match the locked protocol: "
            f"{metadata.get('historical_cutoff')!r} != {expected_cutoff!r}"
        )
    split = assert_window_integrity(connection, cutoff=protocol.primary_cutoff)
    view_counts = connection.execute(
        "SELECT (SELECT count(*) FROM historical_rating_observations), "
        "(SELECT count(*) FROM evaluation_rating_observations)"
    ).fetchone()
    if view_counts is None or (
        int(view_counts[0]) != split["historical_ratings"]
        or int(view_counts[1]) != split["evaluation_ratings"]
    ):
        raise StatisticalAnalysisError(
            "database views do not match an independent cutoff calculation"
        )
    split["rated_sellers"] = int(
        connection.execute(
            "SELECT count(DISTINCT seller_id) FROM seller_rating_observations"
        ).fetchone()[0]
    )
    split["pipeline_metadata"] = metadata
    return split


def _as_json_metrics(data: EvaluationData) -> dict[str, object]:
    raw = error_metrics(
        data.raw_estimates, data.evaluation_means, data.evaluation_counts
    )
    adjusted = error_metrics(
        data.adjusted_estimates, data.evaluation_means, data.evaluation_counts
    )
    improvement = raw.seller_equal_mae - adjusted.seller_equal_mae
    return {
        "seller_count": len(data),
        "historical_rating_count": int(data.historical_counts.sum()),
        "evaluation_rating_count": int(data.evaluation_counts.sum()),
        "raw": metric_dict(raw),
        "adjusted": metric_dict(adjusted),
        "mae_improvement_raw_minus_adjusted": improvement,
        "relative_mae_improvement": (
            improvement / raw.seller_equal_mae if raw.seller_equal_mae != 0 else None
        ),
        "improvement_direction": (
            "adjusted_favored"
            if improvement > 0
            else "raw_favored"
            if improvement < 0
            else "no_difference"
        ),
    }


def _calibration_groups(
    data: EvaluationData,
    lower: np.ndarray,
    upper: np.ndarray,
    covered: np.ndarray,
    *,
    group_by: str,
) -> list[dict[str, object]]:
    counts = (
        data.historical_counts if group_by == "historical" else data.evaluation_counts
    )
    labels = np.asarray([volume_group(int(value)) for value in counts])
    result: list[dict[str, object]] = []
    for group in ("1-4", "5-9", "10-19", "20-49", "50+"):
        mask = labels == group
        if not np.any(mask):
            continue
        result.append(
            {
                "group_by": group_by,
                "volume_group": group,
                "seller_count": int(mask.sum()),
                **summarize_intervals(lower[mask], upper[mask], covered[mask]),
            }
        )
    return result


def _ranking_by_volume(data: EvaluationData) -> list[dict[str, object]]:
    labels = np.asarray([volume_group(int(value)) for value in data.historical_counts])
    rows: list[dict[str, object]] = []
    for group in ("1-4", "5-9", "10-19", "20-49", "50+"):
        mask = labels == group
        if int(mask.sum()) < 2:
            continue
        comparison = ranking_comparison(
            data.raw_estimates[mask],
            data.adjusted_estimates[mask],
            data.seller_ids[mask],
            tuple(k for k in (10, 25, 50) if k <= int(mask.sum())),
        )
        rows.append(
            {
                "volume_group": group,
                "seller_count": int(mask.sum()),
                "spearman_correlation": comparison["spearman_correlation"],
                "kendall_tau_b": comparison["kendall_tau_b"],
                "absolute_rank_change": comparison["absolute_rank_change"],
            }
        )
    return rows


def _write_volume_csv(path: Path, rows: list[dict[str, object]]) -> None:
    columns = [
        "group_by",
        "volume_group",
        "seller_count",
        "historical_rating_count",
        "evaluation_rating_count",
        "raw_seller_equal_mae",
        "adjusted_seller_equal_mae",
        "mae_improvement",
        "raw_seller_equal_rmse",
        "adjusted_seller_equal_rmse",
        "raw_review_weighted_mae",
        "adjusted_review_weighted_mae",
        "raw_review_weighted_rmse",
        "adjusted_review_weighted_rmse",
    ]
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            raw = row["raw"]
            adjusted = row["adjusted"]
            assert isinstance(raw, Mapping) and isinstance(adjusted, Mapping)
            writer.writerow(
                {
                    "group_by": row["group_by"],
                    "volume_group": row["volume_group"],
                    "seller_count": row["seller_count"],
                    "historical_rating_count": row["historical_rating_count"],
                    "evaluation_rating_count": row["evaluation_rating_count"],
                    "raw_seller_equal_mae": raw["seller_equal_mae"],
                    "adjusted_seller_equal_mae": adjusted["seller_equal_mae"],
                    "mae_improvement": row["mae_improvement"],
                    "raw_seller_equal_rmse": raw["seller_equal_rmse"],
                    "adjusted_seller_equal_rmse": adjusted["seller_equal_rmse"],
                    "raw_review_weighted_mae": raw["review_weighted_mae"],
                    "adjusted_review_weighted_mae": adjusted["review_weighted_mae"],
                    "raw_review_weighted_rmse": raw["review_weighted_rmse"],
                    "adjusted_review_weighted_rmse": adjusted["review_weighted_rmse"],
                }
            )


def _stage_directory(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.staging-{uuid4().hex}")


def _publish_directory(staged: Path, destination: Path, *, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        raise StatisticalAnalysisError(
            f"output exists; use --overwrite to replace: {destination}"
        )
    backup = destination.with_name(f".{destination.name}.backup-{uuid4().hex}")
    try:
        if destination.exists():
            os.replace(destination, backup)
        os.replace(staged, destination)
    except OSError as error:
        if destination.exists():
            shutil.rmtree(destination)
        if backup.exists():
            os.replace(backup, destination)
        raise StatisticalAnalysisError(
            f"cannot publish analysis outputs: {error}"
        ) from error
    finally:
        if backup.exists():
            shutil.rmtree(backup)


def _results_markdown(
    model: Mapping[str, Any],
    tuning: Mapping[str, Any],
    uncertainty: Mapping[str, Any],
    ranking: Mapping[str, Any],
    cutoff: str,
) -> str:
    raw = model["predictive_metrics"]["raw"]
    adjusted = model["predictive_metrics"]["adjusted"]
    bootstrap = model["paired_bootstrap"]
    coverage = uncertainty["posterior_predictive"]["overall"]
    improvement = model["predictive_metrics"]["mae_improvement_raw_minus_adjusted"]
    bootstrap_lower = bootstrap["confidence_interval_lower"]
    bootstrap_upper = bootstrap["confidence_interval_upper"]
    spearman = ranking["method_comparison"]["spearman_correlation"]
    kendall = ranking["method_comparison"]["kendall_tau_b"]
    spearman_text = f"{spearman:.6f}" if spearman is not None else "undefined"
    kendall_text = f"{kendall:.6f}" if kendall is not None else "undefined"
    return f"""# Phase 3B Statistical Results

Generated locally from the locked Phase 3A protocol.

## Historical evaluation

- Cutoff: {cutoff}
- Eligible evaluation sellers: {model["predictive_metrics"]["seller_count"]:,}
- Selected prior strength: {tuning["selected_prior_strength"]}
- Fallback used: {str(tuning["fallback_used"]).lower()}
- Raw seller-equal MAE: {raw["seller_equal_mae"]:.6f}
- Adjusted seller-equal MAE: {adjusted["seller_equal_mae"]:.6f}
- MAE improvement (raw minus adjusted): {improvement:.6f}
- 95% paired seller bootstrap interval: [{bootstrap_lower:.6f}, {bootstrap_upper:.6f}]

Positive MAE improvement favors adjustment; negative values favor the arithmetic
mean. This is predictive evidence for later seller-associated order-experience
ratings, not proof of intrinsic seller quality.

## Uncertainty and ranking

- Conditional posterior predictive coverage: {coverage["empirical_coverage"]:.4%}
- Mean predictive interval width: {coverage["mean_width"]:.6f}
- Raw/adjusted Spearman correlation: {spearman_text}
- Raw/adjusted Kendall tau-b: {kendall_text}

Predictive intervals condition retrospectively on each seller's realized future
sample size. Credible intervals describe model-based uncertainty about expected
rating; predictive intervals describe future observed sample means. Posterior
ranking probabilities are conditional on the Dirichlet model and observed data.

## Limitations

Later means are noisy proxies for future expectations. The analysis assumes
conditional exchangeability and temporal stability that may not hold. Review
participation, attribution, seller survival, order mix, dependence, and platform
change remain sources of bias. No causal or objective seller-quality claim is
supported. Real-data-derived outputs remain local pending publication-rights
review under the documented CC BY-NC-SA 4.0 conditions.
"""


def run_statistical_analysis(
    *,
    database: Path,
    config: Path,
    output_directory: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run Phase 3B and safely publish aggregate local reports."""

    if not database.is_file():
        raise StatisticalAnalysisError(f"database does not exist: {database}")
    if output_directory.exists() and not overwrite:
        raise StatisticalAnalysisError(
            f"output exists; use --overwrite to replace: {output_directory}"
        )
    try:
        protocol = load_statistical_protocol(config)
    except StatisticalConfigError as error:
        raise StatisticalAnalysisError(str(error)) from error
    staged = _stage_directory(output_directory)
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(database), read_only=True)
        population = _validate_database(connection, protocol)
        tuning = tune_prior_strength(connection, protocol)
        selected_m = float(tuning["selected_prior_strength"])
        historical = aggregate_sellers(
            connection, end_exclusive=protocol.primary_cutoff
        )
        evaluation = aggregate_sellers(
            connection,
            start_inclusive=protocol.primary_cutoff,
            end_exclusive=date_after_data(connection),
        )
        reference = pooled_reference(connection, end_exclusive=protocol.primary_cutoff)
        paired = build_evaluation_data(
            historical,
            evaluation,
            reference_mean=reference.mean_rating,
            prior_strength=selected_m,
        )
        predictive_metrics = _as_json_metrics(paired)
        bootstrap = paired_mae_bootstrap(
            paired,
            resamples=protocol.bootstrap_resamples,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed,
        )
        model_report: dict[str, Any] = {
            "cutoff": protocol.primary_cutoff.isoformat(),
            "estimand": "future eligible seller-associated order-experience rating",
            "reference": {
                "population": "eligible historical ratings only",
                "rating_count": reference.observation_count,
                "category_counts": reference.category_counts.tolist(),
                "probabilities": reference.probabilities.tolist(),
                "mean_rating": reference.mean_rating,
            },
            "selected_prior_strength": tuning["selected_prior_strength"],
            "predictive_metrics": predictive_metrics,
            "paired_bootstrap": bootstrap,
        }

        historical_alphas = posterior_alpha(
            historical.category_counts, reference.probabilities, selected_m
        )
        evaluation_alphas = posterior_alpha(
            paired.historical_category_counts, reference.probabilities, selected_m
        )
        credible = credible_intervals(
            historical_alphas,
            draws=protocol.posterior_draws,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed,
        )
        equivalence_difference = float(
            np.max(
                np.abs(
                    credible.means
                    - (historical.rating_sums + selected_m * reference.mean_rating)
                    / (historical.review_counts + selected_m)
                )
            )
        )
        if equivalence_difference > 1e-12:
            raise StatisticalAnalysisError(
                "Dirichlet posterior mean does not match the shrinkage estimate"
            )
        predictive = predictive_intervals(
            evaluation_alphas,
            paired.evaluation_counts,
            paired.evaluation_means,
            draws=protocol.posterior_draws,
            interval_level=protocol.interval_level,
            seed=protocol.random_seed + 1,
        )
        uncertainty_report: dict[str, Any] = {
            "method": "empirical_bayes_five_category_dirichlet",
            "interval_level": protocol.interval_level,
            "posterior_draws": protocol.posterior_draws,
            "zero_category_policy": (
                "lower-dimensional Dirichlet on positive pooled-probability "
                "support; zero categories remain exactly zero"
            ),
            "posterior_expected_rating": {
                "seller_count": len(historical),
                "overall": summarize_intervals(credible.lower, credible.upper),
                "maximum_shrinkage_equivalence_difference": equivalence_difference,
            },
            "posterior_predictive": {
                "target": "future seller sample mean",
                "conditional_on_realized_future_sample_size": True,
                "seller_count": len(paired),
                "overall": summarize_intervals(
                    predictive.lower, predictive.upper, predictive.covered
                ),
                "by_historical_volume": _calibration_groups(
                    paired,
                    predictive.lower,
                    predictive.upper,
                    predictive.covered,
                    group_by="historical",
                ),
                "by_evaluation_volume": _calibration_groups(
                    paired,
                    predictive.lower,
                    predictive.upper,
                    predictive.covered,
                    group_by="evaluation",
                ),
            },
            "assumptions": [
                "conditionally exchangeable ratings within each modeled window",
                "stable seller rating distribution between periods",
                "independent categorical draws conditional on seller probabilities",
                "plug-in pooled prior and selected prior strength",
                "review participation and seller survival may be selective",
            ],
        }

        method_ranking = ranking_comparison(
            paired.raw_estimates,
            paired.adjusted_estimates,
            paired.seller_ids,
            protocol.ranking_top_k,
        )
        ranking_stability = posterior_ranking_stability(
            evaluation_alphas,
            paired.seller_ids,
            draws=protocol.posterior_draws,
            top_k_values=protocol.ranking_top_k,
            seed=protocol.random_seed + 2,
        )
        ranking_report: dict[str, Any] = {
            "universe": (
                "same sellers with at least one historical and evaluation rating"
            ),
            "method_comparison": method_ranking,
            "by_historical_volume": _ranking_by_volume(paired),
            "posterior_stability": ranking_stability,
        }
        volume_rows = metrics_by_group(paired, group_by="historical")
        volume_rows.extend(metrics_by_group(paired, group_by="evaluation"))

        processed_at = datetime.now(UTC).isoformat()
        reproducibility: dict[str, Any] = {
            "processing_timestamp_utc": processed_at,
            "application_version": _application_version(),
            "numpy_version": np.__version__,
            "duckdb_version": duckdb.__version__,
            "database_file_name": database.name,
            "database_sha256": sha256_file(database),
            "protocol_file_name": config.name,
            "protocol_sha256": sha256_file(config),
            "protocol_version": protocol.protocol_version,
            "random_seed": protocol.random_seed,
            "derived_random_streams": {
                "credible_intervals": protocol.random_seed,
                "predictive_intervals": protocol.random_seed + 1,
                "posterior_ranking": protocol.random_seed + 2,
                "paired_bootstrap": protocol.random_seed,
            },
            "population_reconciliation": population,
            "output_files": list(OUTPUT_NAMES),
            "privacy": {
                "seller_level_exports": False,
                "review_text_exported": False,
                "customer_identifiers_exported": False,
            },
        }

        staged.mkdir(parents=True, exist_ok=False)
        write_json_artifact(model_report, staged / "model_comparison.json")
        write_json_artifact(tuning, staged / "prior_tuning.json")
        write_json_artifact(uncertainty_report, staged / "uncertainty_summary.json")
        write_json_artifact(ranking_report, staged / "ranking_comparison.json")
        write_json_artifact(reproducibility, staged / "reproducibility.json")
        _write_volume_csv(staged / "evaluation_by_volume.csv", volume_rows)
        markdown = _results_markdown(
            model_report,
            tuning,
            uncertainty_report,
            ranking_report,
            protocol.primary_cutoff.isoformat(),
        )
        (staged / "statistical_results.md").write_text(
            markdown, encoding="utf-8", newline="\n"
        )
        _publish_directory(staged, output_directory, overwrite=overwrite)
        return {
            "model_comparison": model_report,
            "prior_tuning": tuning,
            "uncertainty": uncertainty_report,
            "ranking": ranking_report,
            "reproducibility": reproducibility,
        }
    except StatisticalAnalysisError:
        raise
    except (
        StatisticalConfigError,
        StatisticalDataError,
        duckdb.Error,
        OSError,
        ValueError,
        FloatingPointError,
    ) as error:
        raise StatisticalAnalysisError(
            f"statistical analysis failed: {error}"
        ) from error
    finally:
        if connection is not None:
            connection.close()
        if staged.exists():
            shutil.rmtree(staged)


def date_after_data(connection: duckdb.DuckDBPyConnection) -> date:
    """Return an exclusive date boundary one day beyond the maximum review date."""

    row = connection.execute(
        "SELECT CAST(max(review_answer_timestamp) AS DATE) + INTERVAL 1 DAY "
        "FROM seller_rating_observations"
    ).fetchone()
    if row is None or row[0] is None:
        raise StatisticalAnalysisError("analytical database contains no ratings")
    return row[0].date() if isinstance(row[0], datetime) else row[0]


def build_parser() -> argparse.ArgumentParser:
    """Build the Phase 3B command-line parser."""

    parser = argparse.ArgumentParser(
        description="Run the locked Phase 3B historical rating evaluation."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--config", type=Path, default=Path("config/statistical_protocol.toml")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/local/statistical_analysis"),
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the statistical CLI and return a conventional exit status."""

    args = build_parser().parse_args(argv)
    try:
        result = run_statistical_analysis(
            database=args.database,
            config=args.config,
            output_directory=args.output_dir,
            overwrite=args.overwrite,
        )
    except StatisticalAnalysisError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    model = result["model_comparison"]["predictive_metrics"]
    tuning = result["prior_tuning"]
    print(f"selected prior strength: {tuning['selected_prior_strength']}")
    print(f"evaluation sellers: {model['seller_count']}")
    print(f"raw seller-equal MAE: {model['raw']['seller_equal_mae']:.6f}")
    print(f"adjusted seller-equal MAE: {model['adjusted']['seller_equal_mae']:.6f}")
    print(f"outputs: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
