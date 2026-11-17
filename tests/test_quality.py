"""Tests for reconciled and overlapping quality counters."""

import pytest

from rating_reliability.quality import DataQualitySummary


def test_overlapping_issues_count_once_as_a_rejected_record() -> None:
    summary = DataQualitySummary()
    summary.record_line_status("record")
    summary.record_validation(
        ("missing_review_id", "invalid_review_stars", "invalid_review_date"), ()
    )
    summary.finalize(accepted=0)

    assert summary.processed_records == 1
    assert summary.rejected_records == 1
    assert summary.missing_required_identifiers == 1
    assert summary.invalid_star_ratings == 1
    assert summary.invalid_dates == 1
    assert sum(summary.issue_counts.values()) == 3


def test_blank_lines_are_excluded_from_rates() -> None:
    summary = DataQualitySummary()
    summary.record_line_status("blank_line")
    summary.record_line_status("record")
    summary.record_validation((), ())
    summary.finalize(accepted=1)

    assert summary.total_lines == 2
    assert summary.processed_records == 1
    assert summary.acceptance_rate == 1.0
    assert summary.rejection_rate == 0.0


def test_empty_input_has_zero_rates() -> None:
    summary = DataQualitySummary()
    summary.finalize(accepted=0)

    assert summary.acceptance_rate == 0.0
    assert summary.rejection_rate == 0.0


def test_nonreconciling_counts_are_rejected() -> None:
    summary = DataQualitySummary(total_lines=1)

    with pytest.raises(ValueError, match="do not reconcile"):
        summary.finalize(accepted=0)
