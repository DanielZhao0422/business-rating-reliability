"""Structured, privacy-conscious data-quality accounting."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class DataQualitySummary:
    """Quality counters for one input file.

    Accepted and rejected records are mutually exclusive. Issue counters may
    overlap because one schema-invalid object can violate multiple rules. Blank
    lines are counted but excluded from the record denominator.
    """

    total_lines: int = 0
    blank_lines: int = 0
    successfully_parsed_records: int = 0
    malformed_json_records: int = 0
    non_object_json_records: int = 0
    schema_invalid_records: int = 0
    accepted_records: int = 0
    rejected_records: int = 0
    duplicate_identifiers: int = 0
    identical_duplicates: int = 0
    conflicting_duplicates: int = 0
    missing_required_identifiers: int = 0
    invalid_star_ratings: int = 0
    invalid_dates: int = 0
    orphaned_reviews: int = 0
    missing_optional_field_warnings: int = 0
    issue_counts: Counter[str] = field(default_factory=Counter)

    @property
    def processed_records(self) -> int:
        """Return nonblank lines treated as record attempts."""

        return self.total_lines - self.blank_lines

    @property
    def acceptance_rate(self) -> float:
        """Return accepted records divided by nonblank record attempts."""

        if self.processed_records == 0:
            return 0.0
        return self.accepted_records / self.processed_records

    @property
    def rejection_rate(self) -> float:
        """Return rejected records divided by nonblank record attempts."""

        if self.processed_records == 0:
            return 0.0
        return self.rejected_records / self.processed_records

    def record_line_status(self, status: str) -> None:
        """Update physical-line and parser-level counters."""

        self.total_lines += 1
        if status == "blank_line":
            self.blank_lines += 1
            self.issue_counts[status] += 1
        elif status == "malformed_json":
            self.malformed_json_records += 1
            self.rejected_records += 1
            self.issue_counts[status] += 1
        elif status == "non_object_json":
            self.successfully_parsed_records += 1
            self.non_object_json_records += 1
            self.rejected_records += 1
            self.issue_counts[status] += 1
        elif status == "record":
            self.successfully_parsed_records += 1
        else:
            raise ValueError(f"unknown JSONL status: {status}")

    def record_validation(
        self, errors: tuple[str, ...], warnings: tuple[str, ...]
    ) -> None:
        """Update schema issue counters for one parsed object."""

        self.issue_counts.update(errors)
        self.issue_counts.update(warnings)
        self.missing_optional_field_warnings += len(warnings)

        if errors:
            self.schema_invalid_records += 1
            self.rejected_records += 1
            if any(
                error.startswith("missing_") and error.endswith("_id")
                for error in errors
            ):
                self.missing_required_identifiers += 1
            if "invalid_review_stars" in errors:
                self.invalid_star_ratings += 1
            if any(
                error in {"missing_review_date", "invalid_review_date"}
                for error in errors
            ):
                self.invalid_dates += 1

    def finalize(
        self,
        *,
        accepted: int,
        identical_duplicates: int = 0,
        conflicting_duplicates: int = 0,
        orphaned_reviews: int = 0,
    ) -> None:
        """Add database-derived outcomes and verify record reconciliation."""

        duplicates = identical_duplicates + conflicting_duplicates
        self.accepted_records = accepted
        self.duplicate_identifiers = duplicates
        self.identical_duplicates = identical_duplicates
        self.conflicting_duplicates = conflicting_duplicates
        self.orphaned_reviews = orphaned_reviews
        self.rejected_records += duplicates + orphaned_reviews
        if identical_duplicates:
            self.issue_counts["identical_duplicate"] += identical_duplicates
        if conflicting_duplicates:
            self.issue_counts["conflicting_duplicate"] += conflicting_duplicates
        if orphaned_reviews:
            self.issue_counts["orphaned_review"] += orphaned_reviews

        if self.accepted_records + self.rejected_records != self.processed_records:
            raise ValueError("quality counts do not reconcile")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable summary with derived rates."""

        result = asdict(self)
        result["issue_counts"] = dict(sorted(self.issue_counts.items()))
        result["processed_records"] = self.processed_records
        result["acceptance_rate"] = self.acceptance_rate
        result["rejection_rate"] = self.rejection_rate
        return result


@dataclass(frozen=True)
class QualityReport:
    """Quality results for a complete business-and-review ingestion run."""

    business_input: str
    review_input: str
    batch_size: int
    businesses: DataQualitySummary
    reviews: DataQualitySummary

    def to_dict(self) -> dict[str, object]:
        """Return the stable machine-readable report structure."""

        return {
            "report_schema_version": 1,
            "inputs": {
                "businesses": self.business_input,
                "reviews": self.review_input,
            },
            "batch_size": self.batch_size,
            "counting_rules": {
                "record_denominator": "nonblank physical lines",
                "accepted_and_rejected_are_mutually_exclusive": True,
                "issue_counters_may_overlap": True,
                "duplicate_count_unit": "valid occurrences after the first identifier",
                "blank_lines_excluded_from_rates": True,
            },
            "businesses": self.businesses.to_dict(),
            "reviews": self.reviews.to_dict(),
        }


def staged_report_path(destination: Path) -> Path:
    """Return a unique temporary JSON path beside the report destination."""

    return destination.with_name(
        f".{destination.stem}.staging-{uuid4().hex}{destination.suffix}"
    )


def write_quality_report(report: QualityReport, destination: Path) -> Path:
    """Write a report to a temporary sibling for later atomic replacement."""

    temporary_path = staged_report_path(destination)
    try:
        with temporary_path.open("x", encoding="utf-8", newline="\n") as report_file:
            json.dump(report.to_dict(), report_file, indent=2, sort_keys=True)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path
