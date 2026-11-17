"""Tests for explicit business and review cleaning rules."""

import math
from datetime import date

import pytest

from rating_reliability.validation import validate_business, validate_review


def test_valid_business_is_normalized_without_losing_source_aggregates() -> None:
    result = validate_business(
        {
            "business_id": " biz-1 ",
            "name": " Example Cafe ",
            "city": "Sample City",
            "state": "PA",
            "categories": "Cafes, Tea",
            "stars": 4.5,
            "review_count": 12,
        }
    )

    assert result.is_valid
    assert result.errors == ()
    assert result.warnings == ()
    assert result.record is not None
    assert result.record.business_id == "biz-1"
    assert result.record.name == "Example Cafe"
    assert result.record.source_stars == 4.5
    assert result.record.source_review_count == 12


@pytest.mark.parametrize("business_id", [None, "", "   "])
def test_business_missing_identifier_is_fatal(business_id: object) -> None:
    result = validate_business({"business_id": business_id})

    assert not result.is_valid
    assert "missing_business_id" in result.errors


def test_business_missing_optional_fields_is_accepted_with_warnings() -> None:
    result = validate_business({"business_id": "biz-1"})

    assert result.is_valid
    assert result.record is not None
    assert result.record.name is None
    assert result.record.source_stars is None
    assert len(result.warnings) == 6
    assert "missing_optional_city" in result.warnings


@pytest.mark.parametrize("stars", [0, 6, math.nan, math.inf, "4.5", True])
def test_invalid_business_source_stars_becomes_null_warning(stars: object) -> None:
    result = validate_business({"business_id": "biz-1", "stars": stars})

    assert result.is_valid
    assert result.record is not None
    assert result.record.source_stars is None
    assert "invalid_optional_source_stars" in result.warnings


@pytest.mark.parametrize("review_count", [-1, 2.5, "2", True])
def test_invalid_business_review_count_becomes_null_warning(
    review_count: object,
) -> None:
    result = validate_business({"business_id": "biz-1", "review_count": review_count})

    assert result.is_valid
    assert result.record is not None
    assert result.record.source_review_count is None
    assert "invalid_optional_source_review_count" in result.warnings


@pytest.mark.parametrize(
    ("source_date", "expected"),
    [("2024-02-29", date(2024, 2, 29)), ("2024-03-01 12:30:59", date(2024, 3, 1))],
)
def test_valid_review_preserves_calendar_date(source_date: str, expected: date) -> None:
    result = validate_review(
        {
            "review_id": " rev-1 ",
            "business_id": " biz-1 ",
            "stars": 5,
            "date": source_date,
        }
    )

    assert result.is_valid
    assert result.record is not None
    assert result.record.review_id == "rev-1"
    assert result.record.business_id == "biz-1"
    assert result.record.stars == 5
    assert result.record.date == expected


@pytest.mark.parametrize("stars", [0, 6, 4.0, True, "4", math.nan, math.inf])
def test_invalid_review_stars_are_fatal(stars: object) -> None:
    result = validate_review(
        {
            "review_id": "rev-1",
            "business_id": "biz-1",
            "stars": stars,
            "date": "2024-01-01",
        }
    )

    assert not result.is_valid
    assert "invalid_review_stars" in result.errors


@pytest.mark.parametrize(
    ("field", "reason"),
    [("review_id", "missing_review_id"), ("business_id", "missing_business_id")],
)
def test_review_missing_identifier_is_fatal(field: str, reason: str) -> None:
    record: dict[str, object] = {
        "review_id": "rev-1",
        "business_id": "biz-1",
        "stars": 3,
        "date": "2024-01-01",
    }
    record.pop(field)

    result = validate_review(record)

    assert not result.is_valid
    assert reason in result.errors


@pytest.mark.parametrize(
    ("source_date", "reason"),
    [
        (None, "missing_review_date"),
        ("", "missing_review_date"),
        ("2024-02-30", "invalid_review_date"),
        ("2024-1-01", "invalid_review_date"),
        ("2024-01-01T12:00:00Z", "invalid_review_date"),
    ],
)
def test_invalid_or_missing_review_date_is_fatal(
    source_date: object, reason: str
) -> None:
    result = validate_review(
        {
            "review_id": "rev-1",
            "business_id": "biz-1",
            "stars": 3,
            "date": source_date,
        }
    )

    assert not result.is_valid
    assert reason in result.errors
