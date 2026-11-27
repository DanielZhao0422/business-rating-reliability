"""Structural checks for the prespecified Phase 3 statistical protocol."""

import tomllib
from datetime import date
from pathlib import Path

PROTOCOL_PATH = Path(__file__).parents[1] / "config" / "statistical_protocol.toml"


def _protocol() -> dict[str, object]:
    with PROTOCOL_PATH.open("rb") as file:
        return tomllib.load(file)


def test_statistical_protocol_has_fixed_primary_design() -> None:
    protocol = _protocol()

    assert protocol["phase"] == "3A"
    assert protocol["primary_cutoff"] == date(2018, 1, 1)
    assert protocol["rating_min"] == 1
    assert protocol["rating_max"] == 5
    assert protocol["primary_metric"] == "seller_equal_weight_mae"
    assert protocol["prior_strength_grid"] == [1, 2, 5, 10, 20, 50, 100]
    assert protocol["ranking_top_k"] == [10, 25, 50]
    assert protocol["interval_level"] == 0.95
    assert protocol["bootstrap_resamples"] == 2000
    assert protocol["random_seed"] == 42


def test_internal_validation_precedes_final_evaluation() -> None:
    protocol = _protocol()
    cutoff = protocol["primary_cutoff"]
    folds = protocol["internal_validation_folds"]

    assert isinstance(cutoff, date)
    assert isinstance(folds, list)
    assert folds
    for fold in folds:
        assert fold["training_end_exclusive"] == fold["validation_start_inclusive"]
        assert fold["validation_start_inclusive"] < fold["validation_end_exclusive"]
        assert fold["validation_end_exclusive"] <= cutoff
