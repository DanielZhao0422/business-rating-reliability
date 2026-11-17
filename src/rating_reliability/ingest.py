"""Incremental JSONL ingestion command for business and review data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import duckdb

from rating_reliability.quality import (
    DataQualitySummary,
    QualityReport,
    write_quality_report,
)
from rating_reliability.storage import (
    connect_staging_database,
    finalize_database,
    insert_business_batch,
    insert_review_batch,
    staged_database_path,
)
from rating_reliability.validation import (
    BusinessRecord,
    JsonObject,
    ReviewRecord,
    validate_business,
    validate_review,
)

JsonlStatus = Literal["record", "blank_line", "malformed_json", "non_object_json"]


class IngestionError(Exception):
    """An expected ingestion failure suitable for concise CLI display."""


@dataclass(frozen=True)
class JsonlLine:
    """One physical JSONL line with provenance and safe parse status."""

    line_number: int
    status: JsonlStatus
    record: JsonObject | None = None


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON numeric constant: {value}")


def iter_jsonl(path: Path) -> Iterator[JsonlLine]:
    """Yield UTF-8 JSONL lines incrementally without retaining file contents.

    Malformed content is represented only by a reason status; source content is
    deliberately omitted to avoid leaking review text into diagnostics.
    """

    with path.open("r", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                yield JsonlLine(line_number=line_number, status="blank_line")
                continue
            try:
                value = json.loads(line, parse_constant=_reject_nonstandard_constant)
            except (json.JSONDecodeError, ValueError):
                yield JsonlLine(line_number=line_number, status="malformed_json")
                continue
            if not isinstance(value, dict):
                yield JsonlLine(line_number=line_number, status="non_object_json")
                continue
            yield JsonlLine(line_number=line_number, status="record", record=value)


def iter_jsonl_batches(path: Path, batch_size: int) -> Iterator[tuple[JsonlLine, ...]]:
    """Yield bounded batches of physical JSONL line results."""

    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size <= 0
    ):
        raise ValueError("batch_size must be a positive integer")

    batch: list[JsonlLine] = []
    for item in iter_jsonl(path):
        batch.append(item)
        if len(batch) == batch_size:
            yield tuple(batch)
            batch.clear()
    if batch:
        yield tuple(batch)


def _load_business_candidates(
    path: Path,
    connection: duckdb.DuckDBPyConnection,
    batch_size: int,
) -> DataQualitySummary:
    summary = DataQualitySummary()
    batch: list[tuple[int, BusinessRecord]] = []
    for item in iter_jsonl(path):
        summary.record_line_status(item.status)
        if item.record is None:
            continue
        result = validate_business(item.record)
        summary.record_validation(result.errors, result.warnings)
        if result.is_valid:
            assert result.record is not None
            batch.append((item.line_number, result.record))
            if len(batch) == batch_size:
                insert_business_batch(connection, batch)
                batch.clear()
    insert_business_batch(connection, batch)
    return summary


def _load_review_candidates(
    path: Path,
    connection: duckdb.DuckDBPyConnection,
    batch_size: int,
) -> DataQualitySummary:
    summary = DataQualitySummary()
    batch: list[tuple[int, ReviewRecord]] = []
    for item in iter_jsonl(path):
        summary.record_line_status(item.status)
        if item.record is None:
            continue
        result = validate_review(item.record)
        summary.record_validation(result.errors, result.warnings)
        if result.is_valid:
            assert result.record is not None
            batch.append((item.line_number, result.record))
            if len(batch) == batch_size:
                insert_review_batch(connection, batch)
                batch.clear()
    insert_review_batch(connection, batch)
    return summary


def _validate_paths(
    businesses: Path,
    reviews: Path,
    database: Path,
    quality_report: Path,
    batch_size: int,
    overwrite: bool,
) -> None:
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or batch_size <= 0
    ):
        raise IngestionError("batch size must be a positive integer")
    for label, path in (("businesses", businesses), ("reviews", reviews)):
        if not path.is_file():
            raise IngestionError(f"{label} input file does not exist: {path}")

    resolved_inputs = {businesses.resolve(), reviews.resolve()}
    if (
        database.resolve() in resolved_inputs
        or quality_report.resolve() in resolved_inputs
    ):
        raise IngestionError("output paths must differ from input paths")
    if database.resolve() == quality_report.resolve():
        raise IngestionError("database and quality report paths must differ")

    existing_outputs = [path for path in (database, quality_report) if path.exists()]
    if existing_outputs and not overwrite:
        names = ", ".join(str(path) for path in existing_outputs)
        raise IngestionError(
            f"output already exists; use --overwrite to replace: {names}"
        )


def run_ingestion(
    *,
    businesses: Path,
    reviews: Path,
    database: Path,
    quality_report: Path,
    batch_size: int = 10_000,
    overwrite: bool = False,
) -> QualityReport:
    """Validate two JSONL files and atomically publish a DuckDB and JSON report.

    All parsing and candidate inserts are performed against a temporary sibling
    database. Existing outputs are untouched until ingestion and relational
    validation complete successfully.
    """

    _validate_paths(
        businesses, reviews, database, quality_report, batch_size, overwrite
    )
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        quality_report.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise IngestionError(f"cannot prepare output directories: {error}") from error

    staging_database = staged_database_path(database)
    staging_report: Path | None = None
    connection: duckdb.DuckDBPyConnection | None = None
    published_database = False
    try:
        connection = connect_staging_database(staging_database)
        business_summary = _load_business_candidates(businesses, connection, batch_size)
        review_summary = _load_review_candidates(reviews, connection, batch_size)
        storage_stats = finalize_database(connection)

        business_summary.finalize(
            accepted=storage_stats.accepted_businesses,
            identical_duplicates=storage_stats.identical_business_duplicates,
            conflicting_duplicates=storage_stats.conflicting_business_duplicates,
        )
        review_summary.finalize(
            accepted=storage_stats.accepted_reviews,
            identical_duplicates=storage_stats.identical_review_duplicates,
            conflicting_duplicates=storage_stats.conflicting_review_duplicates,
            orphaned_reviews=storage_stats.orphaned_reviews,
        )
        report = QualityReport(
            business_input=businesses.name,
            review_input=reviews.name,
            batch_size=batch_size,
            businesses=business_summary,
            reviews=review_summary,
        )
        connection.close()
        connection = None

        staging_report = write_quality_report(report, quality_report)

        if not overwrite and (database.exists() or quality_report.exists()):
            raise IngestionError(
                "an output appeared during ingestion; refusing to replace it"
            )
        os.replace(staging_database, database)
        published_database = True
        os.replace(staging_report, quality_report)
        staging_report = None
        return report
    except IngestionError:
        raise
    except (OSError, UnicodeError, duckdb.Error) as error:
        raise IngestionError(f"ingestion failed: {error}") from error
    finally:
        if connection is not None:
            connection.close()
        staging_database.unlink(missing_ok=True)
        Path(f"{staging_database}.wal").unlink(missing_ok=True)
        if staging_report is not None:
            staging_report.unlink(missing_ok=True)
        if published_database and not quality_report.exists():
            # Two output files cannot be replaced as one atomic operation on
            # Windows. The database is still complete and internally valid.
            pass


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the Phase 2A command-line parser."""

    parser = argparse.ArgumentParser(
        description="Validate synthetic Yelp-style JSONL files and build DuckDB tables."
    )
    parser.add_argument(
        "--businesses", required=True, type=Path, help="business JSONL path"
    )
    parser.add_argument("--reviews", required=True, type=Path, help="review JSONL path")
    parser.add_argument(
        "--database", required=True, type=Path, help="output DuckDB path"
    )
    parser.add_argument(
        "--quality-report", required=True, type=Path, help="output quality JSON path"
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=10_000,
        help="maximum validated candidates per database write (default: 10000)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="explicitly replace existing database and report outputs",
    )
    return parser


def _summary_line(label: str, summary: DataQualitySummary) -> str:
    return (
        f"{label}: accepted={summary.accepted_records}, "
        f"rejected={summary.rejected_records}, blank={summary.blank_lines}, "
        f"acceptance_rate={summary.acceptance_rate:.1%}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a conventional process exit code."""

    args = build_parser().parse_args(argv)
    try:
        report = run_ingestion(
            businesses=args.businesses,
            reviews=args.reviews,
            database=args.database,
            quality_report=args.quality_report,
            batch_size=args.batch_size,
            overwrite=args.overwrite,
        )
    except IngestionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(_summary_line("businesses", report.businesses))
    print(_summary_line("reviews", report.reviews))
    print(f"database: {args.database}")
    print(f"quality report: {args.quality_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
