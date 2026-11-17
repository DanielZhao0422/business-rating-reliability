"""Tests for the user-facing ingestion command."""

from pathlib import Path

import pytest

from rating_reliability.ingest import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUSINESSES = PROJECT_ROOT / "data/sample/synthetic_businesses.jsonl"
REVIEWS = PROJECT_ROOT / "data/sample/synthetic_reviews.jsonl"


def _arguments(tmp_path: Path) -> list[str]:
    return [
        "--businesses",
        str(BUSINESSES),
        "--reviews",
        str(REVIEWS),
        "--database",
        str(tmp_path / "cli.duckdb"),
        "--quality-report",
        str(tmp_path / "cli-quality.json"),
        "--batch-size",
        "2",
    ]


def test_cli_success_prints_concise_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(_arguments(tmp_path))
    output = capsys.readouterr()

    assert exit_code == 0
    assert "businesses: accepted=5, rejected=6" in output.out
    assert "reviews: accepted=7, rejected=12" in output.out
    assert output.err == ""


def test_cli_missing_file_returns_nonzero_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = _arguments(tmp_path)
    arguments[1] = str(tmp_path / "missing.jsonl")

    exit_code = main(arguments)
    output = capsys.readouterr()

    assert exit_code == 1
    assert "does not exist" in output.err
    assert "Traceback" not in output.err


def test_cli_output_directory_error_returns_nonzero_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("synthetic blocker", encoding="utf-8")
    arguments = _arguments(tmp_path)
    arguments[5] = str(blocked_parent / "output.duckdb")

    exit_code = main(arguments)
    output = capsys.readouterr()

    assert exit_code == 1
    assert "cannot prepare output directories" in output.err
    assert "Traceback" not in output.err


def test_cli_invalid_batch_size_exits_nonzero(tmp_path: Path) -> None:
    arguments = _arguments(tmp_path)
    arguments[-1] = "0"

    with pytest.raises(SystemExit) as error:
        main(arguments)

    assert error.value.code == 2


def test_cli_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    output = capsys.readouterr()
    assert error.value.code == 0
    assert "--businesses" in output.out
    assert "--quality-report" in output.out
    assert "--overwrite" in output.out
