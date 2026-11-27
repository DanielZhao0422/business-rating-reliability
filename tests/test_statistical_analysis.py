"""End-to-end synthetic tests for the Phase 3B CLI and safe outputs."""

import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from tests.statistical_helpers import create_statistical_database, write_fast_protocol

from rating_reliability.statistical_analysis import (
    StatisticalAnalysisError,
    main,
    run_statistical_analysis,
)

PROJECT_ROOT = Path(__file__).parents[1]


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    database = create_statistical_database(tmp_path / "stats.duckdb")
    config = write_fast_protocol(
        PROJECT_ROOT / "config" / "statistical_protocol.toml",
        tmp_path / "protocol.toml",
    )
    return database, config


def test_statistical_analysis_writes_all_privacy_safe_outputs(tmp_path: Path) -> None:
    database, config = _inputs(tmp_path)
    output = tmp_path / "reports"
    result = run_statistical_analysis(
        database=database, config=config, output_directory=output
    )

    assert {path.name for path in output.iterdir()} == {
        "model_comparison.json",
        "prior_tuning.json",
        "uncertainty_summary.json",
        "ranking_comparison.json",
        "evaluation_by_volume.csv",
        "reproducibility.json",
        "statistical_results.md",
    }
    model = json.loads((output / "model_comparison.json").read_text())
    assert model["predictive_metrics"]["seller_count"] == 2
    metrics = model["predictive_metrics"]
    expected_improvement = (
        metrics["raw"]["seller_equal_mae"] - metrics["adjusted"]["seller_equal_mae"]
    )
    assert metrics["mae_improvement_raw_minus_adjusted"] == pytest.approx(
        expected_improvement
    )
    assert model["paired_bootstrap"]["observed_mae_improvement"] == pytest.approx(
        expected_improvement
    )
    assert result["prior_tuning"]["fallback_used"] is False
    combined = "".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert '"seller_id"' not in combined
    assert "review text" not in combined.lower()


def test_cli_errors_are_concise_and_overwrite_is_explicit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database, config = _inputs(tmp_path)
    output = tmp_path / "reports"
    arguments = [
        "--database",
        str(database),
        "--config",
        str(config),
        "--output-dir",
        str(output),
    ]
    assert main(arguments) == 0
    assert main(arguments) == 1
    captured = capsys.readouterr()
    assert "use --overwrite" in captured.err
    assert "Traceback" not in captured.err
    assert main(["--database", str(tmp_path / "missing.duckdb")]) == 1


def test_invalid_or_incomplete_inputs_fail_before_publication(tmp_path: Path) -> None:
    database, config = _inputs(tmp_path)
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("primary_cutoff = 2018-01-01\n", encoding="utf-8")
    with pytest.raises(StatisticalAnalysisError, match="missing required"):
        run_statistical_analysis(
            database=database,
            config=invalid,
            output_directory=tmp_path / "invalid-output",
        )

    incomplete = tmp_path / "incomplete.duckdb"
    with duckdb.connect(str(incomplete)) as connection:
        connection.execute("CREATE TABLE irrelevant (value INTEGER)")
    with pytest.raises(StatisticalAnalysisError, match="missing required tables"):
        run_statistical_analysis(
            database=incomplete,
            config=config,
            output_directory=tmp_path / "incomplete-output",
        )
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "DELETE FROM pipeline_metadata "
            "WHERE metadata_key = 'rating_availability_field'"
        )
    with pytest.raises(StatisticalAnalysisError, match="rebuild it with olist_prepare"):
        run_statistical_analysis(
            database=database,
            config=config,
            output_directory=tmp_path / "creation-date-output",
        )


def test_failed_overwrite_preserves_previous_results(tmp_path: Path) -> None:
    database, config = _inputs(tmp_path)
    output = tmp_path / "reports"
    run_statistical_analysis(database=database, config=config, output_directory=output)
    before = hashlib.sha256((output / "model_comparison.json").read_bytes()).hexdigest()
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "UPDATE pipeline_metadata SET metadata_value = '2017-01-01' "
            "WHERE metadata_key = 'historical_cutoff'"
        )

    with pytest.raises(StatisticalAnalysisError, match="cutoff"):
        run_statistical_analysis(
            database=database,
            config=config,
            output_directory=output,
            overwrite=True,
        )
    after = hashlib.sha256((output / "model_comparison.json").read_bytes()).hexdigest()
    assert after == before
