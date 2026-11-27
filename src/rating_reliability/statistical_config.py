"""Validated loader for the locked Phase 3 statistical protocol."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


class StatisticalConfigError(ValueError):
    """Raised when the locked statistical configuration is invalid."""


@dataclass(frozen=True)
class ValidationFold:
    """One expanding-window chronological validation fold."""

    name: str
    training_end_exclusive: date
    validation_start_inclusive: date
    validation_end_exclusive: date


@dataclass(frozen=True)
class StatisticalProtocol:
    """Validated research settings required by Phase 3B."""

    protocol_version: str
    phase: str
    status: str
    primary_cutoff: date
    rating_min: int
    rating_max: int
    historical_min_ratings: int
    evaluation_min_ratings: int
    primary_metric: str
    reference_mean: str
    prior_strength_grid: tuple[float, ...]
    prior_strength_fallback: float
    prior_tuning_metric: str
    prior_tie_tolerance: float
    prior_tie_break: str
    ranking_top_k: tuple[int, ...]
    ranking_tie_method: str
    top_k_tie_break: str
    interval_level: float
    uncertainty_model: str
    posterior_draws: int
    bootstrap_resamples: int
    bootstrap_interval: str
    random_seed: int
    historical_volume_groups: tuple[str, ...]
    evaluation_volume_groups: tuple[str, ...]
    sensitivity_historical_min_ratings: tuple[int, ...]
    secondary_evaluation_min_ratings: tuple[int, ...]
    internal_validation_folds: tuple[ValidationFold, ...]
    minimum_sellers_per_fold: int
    minimum_seller_fold_units: int


_REQUIRED_KEYS = {
    "protocol_version",
    "phase",
    "status",
    "primary_cutoff",
    "rating_min",
    "rating_max",
    "historical_min_ratings",
    "evaluation_min_ratings",
    "primary_metric",
    "reference_mean",
    "prior_strength_grid",
    "prior_strength_fallback",
    "prior_tuning_metric",
    "prior_tie_tolerance",
    "prior_tie_break",
    "ranking_top_k",
    "ranking_tie_method",
    "top_k_tie_break",
    "interval_level",
    "uncertainty_model",
    "posterior_draws",
    "bootstrap_resamples",
    "bootstrap_interval",
    "random_seed",
    "historical_volume_groups",
    "evaluation_volume_groups",
    "sensitivity_historical_min_ratings",
    "secondary_evaluation_min_ratings",
    "internal_validation_folds",
    "tuning_adequacy",
    "method_comparison",
}


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StatisticalConfigError(f"{name} must be a positive integer")
    return value


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatisticalConfigError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise StatisticalConfigError(f"{name} must be a positive finite number")
    return result


def _count_thresholds(value: Any, name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise StatisticalConfigError(f"{name} must be a nonempty array")
    thresholds = tuple(_positive_integer(item, name) for item in value)
    if tuple(sorted(set(thresholds))) != thresholds:
        raise StatisticalConfigError(f"{name} must be increasing and unique")
    return thresholds


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StatisticalConfigError(f"{name} must be a nonempty string")
    return value


def _date(value: Any, name: str) -> date:
    if not isinstance(value, date):
        raise StatisticalConfigError(f"{name} must be a TOML local date")
    return value


def _folds(value: Any, cutoff: date) -> tuple[ValidationFold, ...]:
    if not isinstance(value, list) or not value:
        raise StatisticalConfigError("internal_validation_folds must be nonempty")
    folds: list[ValidationFold] = []
    names: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise StatisticalConfigError(
                f"internal_validation_folds[{index}] must be a table"
            )
        fold = ValidationFold(
            name=_string(raw.get("name"), f"fold {index} name"),
            training_end_exclusive=_date(
                raw.get("training_end_exclusive"),
                f"fold {index} training_end_exclusive",
            ),
            validation_start_inclusive=_date(
                raw.get("validation_start_inclusive"),
                f"fold {index} validation_start_inclusive",
            ),
            validation_end_exclusive=_date(
                raw.get("validation_end_exclusive"),
                f"fold {index} validation_end_exclusive",
            ),
        )
        if fold.name in names:
            raise StatisticalConfigError(f"duplicate validation fold: {fold.name}")
        if fold.training_end_exclusive != fold.validation_start_inclusive:
            raise StatisticalConfigError(
                f"validation fold {fold.name} is not contiguous at its boundary"
            )
        if not fold.validation_start_inclusive < fold.validation_end_exclusive:
            raise StatisticalConfigError(
                f"validation fold {fold.name} has an empty validation window"
            )
        if fold.validation_end_exclusive > cutoff:
            raise StatisticalConfigError(
                f"validation fold {fold.name} extends beyond the final cutoff"
            )
        names.add(fold.name)
        folds.append(fold)
    return tuple(folds)


def load_statistical_protocol(path: Path) -> StatisticalProtocol:
    """Load and strictly validate the Phase 3 protocol TOML."""

    try:
        with path.open("rb") as source:
            raw = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise StatisticalConfigError(
            f"cannot load statistical config: {error}"
        ) from error

    missing = sorted(_REQUIRED_KEYS - raw.keys())
    if missing:
        raise StatisticalConfigError(
            f"statistical config is missing required settings: {', '.join(missing)}"
        )
    cutoff = _date(raw["primary_cutoff"], "primary_cutoff")
    rating_min = _positive_integer(raw["rating_min"], "rating_min")
    rating_max = _positive_integer(raw["rating_max"], "rating_max")
    if (rating_min, rating_max) != (1, 5):
        raise StatisticalConfigError("rating bounds must remain locked at 1 and 5")

    grid_raw = raw["prior_strength_grid"]
    if not isinstance(grid_raw, list) or not grid_raw:
        raise StatisticalConfigError("prior_strength_grid must be a nonempty array")
    grid = tuple(
        _positive_number(value, f"prior_strength_grid[{index}]")
        for index, value in enumerate(grid_raw)
    )
    if tuple(sorted(set(grid))) != grid:
        raise StatisticalConfigError(
            "prior_strength_grid must be strictly increasing and unique"
        )

    top_k_raw = raw["ranking_top_k"]
    if not isinstance(top_k_raw, list) or not top_k_raw:
        raise StatisticalConfigError("ranking_top_k must be a nonempty array")
    top_k = tuple(
        _positive_integer(value, f"ranking_top_k[{index}]")
        for index, value in enumerate(top_k_raw)
    )
    if tuple(sorted(set(top_k))) != top_k:
        raise StatisticalConfigError("ranking_top_k must be increasing and unique")

    level = raw["interval_level"]
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        raise StatisticalConfigError("interval_level must be numeric")
    level = float(level)
    if not 0 < level < 1:
        raise StatisticalConfigError("interval_level must be strictly between 0 and 1")

    strings = {
        key: tuple(raw[key])
        for key in ("historical_volume_groups", "evaluation_volume_groups")
        if isinstance(raw[key], list)
        and raw[key]
        and all(isinstance(item, str) and item for item in raw[key])
    }
    if len(strings) != 2:
        raise StatisticalConfigError("volume groups must be nonempty string arrays")
    adequacy = raw["tuning_adequacy"]
    if not isinstance(adequacy, dict):
        raise StatisticalConfigError("tuning_adequacy must be a table")
    comparison = raw["method_comparison"]
    if (
        not isinstance(comparison, dict)
        or comparison.get("contrast") != "raw_mae_minus_adjusted_mae"
        or comparison.get("lower_is_better") is not False
    ):
        raise StatisticalConfigError(
            "method_comparison must define raw MAE minus adjusted MAE, "
            "with higher better"
        )

    protocol = StatisticalProtocol(
        protocol_version=_string(raw["protocol_version"], "protocol_version"),
        phase=_string(raw["phase"], "phase"),
        status=_string(raw["status"], "status"),
        primary_cutoff=cutoff,
        rating_min=rating_min,
        rating_max=rating_max,
        historical_min_ratings=_positive_integer(
            raw["historical_min_ratings"], "historical_min_ratings"
        ),
        evaluation_min_ratings=_positive_integer(
            raw["evaluation_min_ratings"], "evaluation_min_ratings"
        ),
        primary_metric=_string(raw["primary_metric"], "primary_metric"),
        reference_mean=_string(raw["reference_mean"], "reference_mean"),
        prior_strength_grid=grid,
        prior_strength_fallback=_positive_number(
            raw["prior_strength_fallback"], "prior_strength_fallback"
        ),
        prior_tuning_metric=_string(raw["prior_tuning_metric"], "prior_tuning_metric"),
        prior_tie_tolerance=_positive_number(
            raw["prior_tie_tolerance"], "prior_tie_tolerance"
        ),
        prior_tie_break=_string(raw["prior_tie_break"], "prior_tie_break"),
        ranking_top_k=top_k,
        ranking_tie_method=_string(raw["ranking_tie_method"], "ranking_tie_method"),
        top_k_tie_break=_string(raw["top_k_tie_break"], "top_k_tie_break"),
        interval_level=level,
        uncertainty_model=_string(raw["uncertainty_model"], "uncertainty_model"),
        posterior_draws=_positive_integer(raw["posterior_draws"], "posterior_draws"),
        bootstrap_resamples=_positive_integer(
            raw["bootstrap_resamples"], "bootstrap_resamples"
        ),
        bootstrap_interval=_string(raw["bootstrap_interval"], "bootstrap_interval"),
        random_seed=raw["random_seed"],
        historical_volume_groups=strings["historical_volume_groups"],
        evaluation_volume_groups=strings["evaluation_volume_groups"],
        sensitivity_historical_min_ratings=_count_thresholds(
            raw["sensitivity_historical_min_ratings"],
            "sensitivity_historical_min_ratings",
        ),
        secondary_evaluation_min_ratings=_count_thresholds(
            raw["secondary_evaluation_min_ratings"], "secondary_evaluation_min_ratings"
        ),
        internal_validation_folds=_folds(raw["internal_validation_folds"], cutoff),
        minimum_sellers_per_fold=_positive_integer(
            adequacy.get("minimum_sellers_per_fold"), "minimum_sellers_per_fold"
        ),
        minimum_seller_fold_units=_positive_integer(
            adequacy.get("minimum_seller_fold_units"),
            "minimum_seller_fold_units",
        ),
    )
    if isinstance(protocol.random_seed, bool) or not isinstance(
        protocol.random_seed, int
    ):
        raise StatisticalConfigError("random_seed must be an integer")
    if protocol.prior_strength_fallback not in protocol.prior_strength_grid:
        raise StatisticalConfigError(
            "prior_strength_fallback must be a member of prior_strength_grid"
        )
    locked_values = {
        "phase": (protocol.phase, "3A"),
        "primary_metric": (protocol.primary_metric, "seller_equal_weight_mae"),
        "reference_mean": (
            protocol.reference_mean,
            "pooled_historical_rating_mean",
        ),
        "prior_tuning_metric": (
            protocol.prior_tuning_metric,
            "seller_equal_weight_mae",
        ),
        "prior_tie_break": (protocol.prior_tie_break, "smallest_prior_strength"),
        "ranking_tie_method": (protocol.ranking_tie_method, "average_rank"),
        "top_k_tie_break": (
            protocol.top_k_tie_break,
            "estimate_desc_then_seller_id_asc",
        ),
        "uncertainty_model": (
            protocol.uncertainty_model,
            "empirical_bayes_dirichlet_categorical",
        ),
        "bootstrap_interval": (
            protocol.bootstrap_interval,
            "paired_seller_percentile",
        ),
    }
    for name, (observed, expected) in locked_values.items():
        if observed != expected:
            raise StatisticalConfigError(f"{name} must remain locked at {expected!r}")
    return protocol
