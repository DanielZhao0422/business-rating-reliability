"""Validation tests for the locked statistical protocol loader."""

from pathlib import Path

import pytest

from rating_reliability.statistical_config import (
    StatisticalConfigError,
    load_statistical_protocol,
)

PROJECT_ROOT = Path(__file__).parents[1]


def test_project_protocol_loads_locked_values() -> None:
    protocol = load_statistical_protocol(
        PROJECT_ROOT / "config" / "statistical_protocol.toml"
    )

    assert protocol.primary_cutoff.isoformat() == "2018-01-01"
    assert protocol.prior_strength_grid == (1, 2, 5, 10, 20, 50, 100)
    assert protocol.prior_strength_fallback == 10
    assert protocol.ranking_top_k == (10, 25, 50)
    assert protocol.interval_level == 0.95
    assert protocol.bootstrap_resamples == 2000
    assert protocol.random_seed == 42


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            'primary_metric = "seller_equal_weight_mae"',
            'primary_metric = "rmse"',
            "primary_metric",
        ),
        ("prior_strength_fallback = 10", "prior_strength_fallback = 0", "positive"),
        (
            "prior_strength_fallback = 10",
            "prior_strength_fallback = 3",
            "member",
        ),
        ("interval_level = 0.95", "interval_level = 1.0", "between"),
        ("bootstrap_resamples = 2000", "bootstrap_resamples = 0", "positive"),
        ("random_seed = 42", 'random_seed = "42"', "integer"),
        (
            "prior_strength_grid = [1, 2, 5, 10, 20, 50, 100]",
            "prior_strength_grid = [2, 1]",
            "increasing",
        ),
        ("ranking_top_k = [10, 25, 50]", "ranking_top_k = [25, 10]", "increasing"),
        (
            'contrast = "raw_mae_minus_adjusted_mae"',
            'contrast = "adjusted_error_minus_arithmetic_error"',
            "raw MAE minus adjusted MAE",
        ),
        ("lower_is_better = false", "lower_is_better = true", "higher better"),
    ],
)
def test_invalid_locked_settings_fail_usefully(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    text = (PROJECT_ROOT / "config" / "statistical_protocol.toml").read_text(
        encoding="utf-8"
    )
    assert old in text
    path = tmp_path / "invalid.toml"
    path.write_text(text.replace(old, new), encoding="utf-8")

    with pytest.raises(StatisticalConfigError, match=message):
        load_statistical_protocol(path)
