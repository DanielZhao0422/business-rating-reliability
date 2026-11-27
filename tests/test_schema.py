"""Tests for bounded, privacy-safe source schema inspection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rating_reliability.schema import (
    EXPECTATIONS,
    SchemaInspectionError,
    inspect_schema,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_REVIEWS = PROJECT_ROOT / "data/sample/synthetic_reviews.jsonl"
EMPTY_REVIEWS = PROJECT_ROOT / "data/sample/empty_reviews.jsonl"


def test_jsonl_schema_inspection_is_bounded_and_distinguishes_assumptions() -> None:
    report = inspect_schema(SAMPLE_REVIEWS, record_type="review", sample_size=5)

    assert report["expectation"]["basis"].startswith("configured assumption")
    assert report["expectation"]["canonical_mapping"]["stars"] == "stars"
    observation = report["observation"]
    assert observation["bounded_sample"] is True
    assert observation["sample_limit"] == 5
    assert observation["records_sampled"] == 5
    assert observation["physical_lines_scanned"] == 5
    assert observation["missing_expected_fields"] == []
    assert observation["unexpected_fields"] == []


def test_schema_reports_missing_unexpected_nullable_and_inconsistent_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "reviews.jsonl"
    source.write_text(
        "\n".join(
            [
                '{"review_id":"one","business_id":"biz","stars":5,"date":"2024-01-01","extra":1}',
                '{"review_id":"two","business_id":"biz","stars":"4"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = inspect_schema(source, record_type="review", sample_size=10)
    observation = report["observation"]

    assert observation["unexpected_fields"] == ["extra"]
    assert observation["missing_expected_fields"] == []
    assert observation["inconsistent_type_fields"] == ["stars"]
    assert observation["fields"]["date"]["missing_count"] == 1
    assert observation["fields"]["date"]["nullable_observed"] is True
    assert observation["fields"]["stars"]["observed_types"] == {
        "integer": 1,
        "string": 1,
    }


def test_csv_inspection_supports_source_format_adaptation(tmp_path: Path) -> None:
    source = tmp_path / "businesses.csv"
    source.write_text(
        "business_id,name,city,state,categories,stars,review_count,new_field\n"
        "biz-1,Example Cafe,Sample City,PA,Cafes,4.5,10,x\n"
        "biz-2,Second Cafe,,,,3.0,2,y\n",
        encoding="utf-8",
    )

    report = inspect_schema(source, record_type="business", sample_size=10)
    observation = report["observation"]

    assert report["input"]["format"] == "csv"
    assert observation["records_sampled"] == 2
    assert observation["unexpected_fields"] == ["new_field"]
    assert observation["fields"]["city"]["null_count"] == 1
    assert observation["fields"]["stars"]["observed_types"] == {"string": 2}
    assert report["expectation"]["canonical_mapping"]["source_stars"] == "stars"


def test_header_only_csv_still_reports_observed_source_fields(tmp_path: Path) -> None:
    source = tmp_path / "reviews.csv"
    source.write_text(
        "review_id,business_id,stars,date\n",
        encoding="utf-8",
    )

    report = inspect_schema(source, record_type="review")

    observation = report["observation"]
    assert observation["records_sampled"] == 0
    assert observation["observed_fields"] == [
        "business_id",
        "date",
        "review_id",
        "stars",
    ]
    assert observation["missing_expected_fields"] == []
    assert observation["physical_lines_scanned"] is None
    assert observation["csv_rows_scanned"] == 0


def test_empty_input_reports_all_expected_fields_as_unobserved() -> None:
    report = inspect_schema(EMPTY_REVIEWS, record_type="review")

    assert report["observation"]["records_sampled"] == 0
    assert report["observation"]["missing_expected_fields"] == sorted(
        EXPECTATIONS["review"].expected_fields
    )


def test_schema_report_never_includes_source_field_values(tmp_path: Path) -> None:
    secret = "PRIVATE_REVIEW_CONTENT_MUST_NOT_APPEAR"
    source = tmp_path / "private.jsonl"
    source.write_text(
        json.dumps(
            {
                "review_id": "rev-secret",
                "business_id": "biz-secret",
                "stars": 5,
                "date": "2024-01-01",
                "text": secret,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = inspect_schema(source, record_type="review")

    serialized = json.dumps(report)
    assert secret not in serialized
    assert "text" in report["observation"]["unexpected_fields"]
    assert report["privacy"]["field_values_included"] is False


@pytest.mark.parametrize("sample_size", [0, -1, True])
def test_schema_rejects_invalid_sample_sizes(sample_size: int) -> None:
    with pytest.raises(SchemaInspectionError, match="positive integer"):
        inspect_schema(SAMPLE_REVIEWS, record_type="review", sample_size=sample_size)


def test_schema_cli_writes_reproducible_json_and_protects_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    base_arguments = [
        "--input",
        str(SAMPLE_REVIEWS),
        "--record-type",
        "review",
        "--sample-size",
        "4",
    ]

    assert main([*base_arguments, "--output", str(first)]) == 0
    assert main([*base_arguments, "--output", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes()
    assert main([*base_arguments, "--output", str(first)]) == 1

    output = capsys.readouterr()
    assert "records sampled: 4" in output.out
    assert "use --overwrite" in output.err


def test_schema_cli_handles_unavailable_input_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--record-type",
            "review",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "does not exist" in output.err
    assert "Traceback" not in output.err
