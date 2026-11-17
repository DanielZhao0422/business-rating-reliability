"""Tests for incremental, privacy-conscious JSONL processing."""

from pathlib import Path

import pytest

from rating_reliability.ingest import iter_jsonl, iter_jsonl_batches


def test_iter_jsonl_reports_statuses_and_source_line_numbers(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(
        '{"business_id": "one"}\n\n{"broken":\n[1, 2]\n', encoding="utf-8"
    )

    results = list(iter_jsonl(source))

    assert [result.line_number for result in results] == [1, 2, 3, 4]
    assert [result.status for result in results] == [
        "record",
        "blank_line",
        "malformed_json",
        "non_object_json",
    ]
    assert results[0].record == {"business_id": "one"}
    assert results[2].record is None


def test_iter_jsonl_empty_file_yields_no_records(tmp_path: Path) -> None:
    source = tmp_path / "empty.jsonl"
    source.touch()

    assert list(iter_jsonl(source)) == []


def test_iter_jsonl_rejects_nonstandard_numeric_constants(tmp_path: Path) -> None:
    source = tmp_path / "constant.jsonl"
    source.write_text('{"stars": NaN}\n', encoding="utf-8")

    result = next(iter_jsonl(source))

    assert result.status == "malformed_json"
    assert result.record is None


def test_iter_jsonl_batches_remain_bounded(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text("{}\n{}\n{}\n{}\n{}\n", encoding="utf-8")

    batches = list(iter_jsonl_batches(source, batch_size=2))

    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert [item.line_number for batch in batches for item in batch] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("batch_size", [0, -1, True])
def test_iter_jsonl_batches_rejects_invalid_batch_size(
    tmp_path: Path, batch_size: int
) -> None:
    source = tmp_path / "records.jsonl"
    source.touch()

    with pytest.raises(ValueError, match="positive integer"):
        list(iter_jsonl_batches(source, batch_size))
