"""Bounded, privacy-safe schema inspection for local JSONL and CSV inputs."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Literal

from rating_reliability.artifacts import ArtifactError, sha256_file, write_json_artifact
from rating_reliability.ingest import iter_jsonl

InputFormat = Literal["jsonl", "csv"]


class SchemaInspectionError(Exception):
    """An expected schema-inspection failure for concise CLI display."""


@dataclass(frozen=True)
class SchemaExpectation:
    """Configured source assumptions and canonical field mappings."""

    name: str
    expected_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    canonical_mapping: Mapping[str, str]

    @property
    def optional_fields(self) -> tuple[str, ...]:
        """Return expected fields not designated as required."""

        required = set(self.required_fields)
        return tuple(field for field in self.expected_fields if field not in required)


EXPECTATIONS = {
    "business": SchemaExpectation(
        name="yelp_style_business",
        expected_fields=(
            "business_id",
            "name",
            "city",
            "state",
            "categories",
            "stars",
            "review_count",
        ),
        required_fields=("business_id",),
        canonical_mapping={
            "business_id": "business_id",
            "name": "name",
            "city": "city",
            "state": "state",
            "categories": "categories",
            "source_stars": "stars",
            "source_review_count": "review_count",
        },
    ),
    "review": SchemaExpectation(
        name="yelp_style_review",
        expected_fields=("review_id", "business_id", "stars", "date"),
        required_fields=("review_id", "business_id", "stars", "date"),
        canonical_mapping={
            "review_id": "review_id",
            "business_id": "business_id",
            "stars": "stars",
            "date": "date",
        },
    ),
}


@dataclass
class _FieldStats:
    presence_count: int = 0
    null_count: int = 0
    types: Counter[str] | None = None

    def __post_init__(self) -> None:
        if self.types is None:
            self.types = Counter()


def _value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _detect_format(path: Path, requested: str) -> InputFormat:
    if requested in {"jsonl", "csv"}:
        return requested  # type: ignore[return-value]
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".json"}:
        return "jsonl"
    if suffix == ".csv":
        return "csv"
    raise SchemaInspectionError(
        "cannot detect input format; use --format jsonl or --format csv"
    )


def _inspect_csv(path: Path, sample_size: int) -> tuple[int, dict[str, _FieldStats]]:
    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            return 0, {}
        fields = {str(field): _FieldStats() for field in reader.fieldnames}
        sampled = 0
        for row in islice(reader, sample_size):
            sampled += 1
            for field in fields:
                value = row.get(field)
                normalized = None if value is None or value == "" else value
                stats = fields[field]
                stats.presence_count += 1
                value_type = _value_type(normalized)
                assert stats.types is not None
                stats.types[value_type] += 1
                if normalized is None:
                    stats.null_count += 1
        return sampled, fields


def _inspect_records(
    records: Iterator[dict[str, object]], sample_size: int
) -> tuple[int, dict[str, _FieldStats]]:
    sampled = 0
    fields: dict[str, _FieldStats] = {}
    for record in islice(records, sample_size):
        sampled += 1
        for field, value in record.items():
            stats = fields.setdefault(field, _FieldStats())
            stats.presence_count += 1
            value_type = _value_type(value)
            assert stats.types is not None
            stats.types[value_type] += 1
            if value is None:
                stats.null_count += 1
    return sampled, fields


def inspect_schema(
    path: Path,
    *,
    record_type: str,
    sample_size: int = 1_000,
    input_format: str = "auto",
) -> dict[str, object]:
    """Inspect a bounded sample without retaining or reporting field values."""

    if not path.is_file():
        raise SchemaInspectionError(f"input file does not exist: {path}")
    if record_type not in EXPECTATIONS:
        raise SchemaInspectionError(f"unsupported record type: {record_type}")
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or sample_size <= 0
    ):
        raise SchemaInspectionError("sample size must be a positive integer")

    expectation = EXPECTATIONS[record_type]
    detected_format = _detect_format(path, input_format)
    parser_issues: Counter[str] = Counter()
    lines_scanned: int | None = 0
    csv_rows_scanned: int | None = None

    if detected_format == "jsonl":

        def json_records() -> Iterator[dict[str, object]]:
            nonlocal lines_scanned
            for item in iter_jsonl(path):
                lines_scanned += 1
                if item.record is None:
                    parser_issues[item.status] += 1
                    continue
                yield item.record

        sampled, fields = _inspect_records(json_records(), sample_size)
    else:
        sampled, fields = _inspect_csv(path, sample_size)
        lines_scanned = None
        csv_rows_scanned = sampled

    observed_fields = sorted(fields)
    expected_fields = set(expectation.expected_fields)
    observed_set = set(observed_fields)
    field_report: dict[str, object] = {}
    inconsistent_fields: list[str] = []
    for field in sorted(expected_fields | observed_set):
        stats = fields.get(field, _FieldStats())
        types = dict(sorted((stats.types or Counter()).items()))
        nonnull_types = {name for name in types if name != "null"}
        if len(nonnull_types) > 1:
            inconsistent_fields.append(field)
        missing_count = sampled - stats.presence_count
        field_report[field] = {
            "presence_count": stats.presence_count,
            "missing_count": missing_count,
            "null_count": stats.null_count,
            "nullable_observed": missing_count > 0 or stats.null_count > 0,
            "observed_types": types,
        }

    return {
        "report_schema_version": 1,
        "input": {
            "file_name": path.name,
            "format": detected_format,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        },
        "expectation": {
            "name": expectation.name,
            "basis": "configured assumption; not inferred from the input",
            "expected_fields": list(expectation.expected_fields),
            "required_fields": list(expectation.required_fields),
            "optional_fields": list(expectation.optional_fields),
            "canonical_mapping": dict(expectation.canonical_mapping),
        },
        "observation": {
            "bounded_sample": True,
            "sample_limit": sample_size,
            "records_sampled": sampled,
            "physical_lines_scanned": lines_scanned,
            "csv_rows_scanned": csv_rows_scanned,
            "parser_issue_counts": dict(sorted(parser_issues.items())),
            "observed_fields": observed_fields,
            "missing_expected_fields": sorted(expected_fields - observed_set),
            "unexpected_fields": sorted(observed_set - expected_fields),
            "inconsistent_type_fields": inconsistent_fields,
            "fields": field_report,
        },
        "privacy": {
            "field_values_included": False,
            "review_text_included": False,
        },
    }


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the local schema-inspection CLI parser."""

    parser = argparse.ArgumentParser(
        description="Inspect a bounded, privacy-safe sample of a local source file."
    )
    parser.add_argument("--input", required=True, type=Path, help="local input path")
    parser.add_argument(
        "--record-type",
        required=True,
        choices=sorted(EXPECTATIONS),
        help="expected record schema",
    )
    parser.add_argument(
        "--format",
        choices=("auto", "jsonl", "csv"),
        default="auto",
        help="input format (default: infer from extension)",
    )
    parser.add_argument(
        "--sample-size",
        type=_positive_integer,
        default=1_000,
        help="maximum object records to inspect (default: 1000)",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="local JSON report path"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace an existing report"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run schema inspection and return a conventional process exit code."""

    args = build_parser().parse_args(argv)
    try:
        report = inspect_schema(
            args.input,
            record_type=args.record_type,
            sample_size=args.sample_size,
            input_format=args.format,
        )
        write_json_artifact(report, args.output, overwrite=args.overwrite)
    except (SchemaInspectionError, ArtifactError, OSError, UnicodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    observation = report["observation"]
    assert isinstance(observation, dict)
    print(f"records sampled: {observation['records_sampled']}")
    print(f"missing expected fields: {len(observation['missing_expected_fields'])}")
    print(f"unexpected fields: {len(observation['unexpected_fields'])}")
    print(f"schema report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
