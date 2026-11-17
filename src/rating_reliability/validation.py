"""Schema validation and normalization for business and review records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from numbers import Real

JsonObject = dict[str, object]


@dataclass(frozen=True)
class BusinessRecord:
    """Normalized fields retained for one valid business."""

    business_id: str
    name: str | None
    city: str | None
    state: str | None
    categories: str | None
    source_stars: float | None
    source_review_count: int | None


@dataclass(frozen=True)
class ReviewRecord:
    """Normalized fields retained for one valid review."""

    review_id: str
    business_id: str
    stars: int
    date: date


@dataclass(frozen=True)
class ValidationResult[RecordT]:
    """A validated record or its fatal errors, plus nonfatal warnings."""

    record: RecordT | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        """Return whether validation produced an accepted candidate record."""

        return self.record is not None and not self.errors


def _required_identifier(value: object, field: str) -> tuple[str | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"missing_{field}"
    if not isinstance(value, str):
        return None, f"invalid_{field}_type"
    return value.strip(), None


def _optional_text(record: JsonObject, field: str) -> tuple[str | None, str | None]:
    if field not in record or record[field] is None:
        return None, f"missing_optional_{field}"
    value = record[field]
    if not isinstance(value, str):
        return None, f"invalid_optional_{field}"
    normalized = value.strip()
    if not normalized:
        return None, f"missing_optional_{field}"
    return normalized, None


def _source_stars(value: object) -> tuple[float | None, str | None]:
    if value is None:
        return None, "missing_optional_source_stars"
    if isinstance(value, bool) or not isinstance(value, Real):
        return None, "invalid_optional_source_stars"
    normalized = float(value)
    if not math.isfinite(normalized) or not 1.0 <= normalized <= 5.0:
        return None, "invalid_optional_source_stars"
    return normalized, None


def _source_review_count(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, "missing_optional_source_review_count"
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, "invalid_optional_source_review_count"
    return value, None


def validate_business(record: JsonObject) -> ValidationResult[BusinessRecord]:
    """Validate and normalize one business JSON object.

    A missing or invalid identifier is fatal. Optional text and source aggregate
    problems become warnings and the affected value is stored as ``NULL``.
    Identifiers and valid text fields have surrounding whitespace removed.
    """

    errors: list[str] = []
    warnings: list[str] = []

    business_id, identifier_error = _required_identifier(
        record.get("business_id"), "business_id"
    )
    if identifier_error:
        errors.append(identifier_error)

    optional_values: dict[str, str | None] = {}
    for field in ("name", "city", "state", "categories"):
        value, warning = _optional_text(record, field)
        optional_values[field] = value
        if warning:
            warnings.append(warning)

    source_stars, stars_warning = _source_stars(record.get("stars"))
    if stars_warning:
        warnings.append(stars_warning)

    source_review_count, count_warning = _source_review_count(
        record.get("review_count")
    )
    if count_warning:
        warnings.append(count_warning)

    if errors:
        return ValidationResult(
            record=None, errors=tuple(errors), warnings=tuple(warnings)
        )

    assert business_id is not None
    return ValidationResult(
        record=BusinessRecord(
            business_id=business_id,
            name=optional_values["name"],
            city=optional_values["city"],
            state=optional_values["state"],
            categories=optional_values["categories"],
            source_stars=source_stars,
            source_review_count=source_review_count,
        ),
        warnings=tuple(warnings),
    )


def _review_stars(value: object) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "invalid_review_stars"
    if not 1 <= value <= 5:
        return None, "invalid_review_stars"
    return value, None


def _review_date(value: object) -> tuple[date | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing_review_date"
    if not isinstance(value, str):
        return None, "invalid_review_date"

    candidate = value.strip()
    for date_format in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(candidate, date_format)
        except ValueError:
            continue
        if parsed.strftime(date_format) == candidate:
            return parsed.date(), None
    return None, "invalid_review_date"


def validate_review(record: JsonObject) -> ValidationResult[ReviewRecord]:
    """Validate and normalize one review JSON object.

    Review identifiers, an integer 1--5 rating, and a valid source-format date
    are all required. Source timestamps are reduced to their calendar date; no
    timezone or time-of-day is invented.
    """

    errors: list[str] = []

    review_id, review_id_error = _required_identifier(
        record.get("review_id"), "review_id"
    )
    business_id, business_id_error = _required_identifier(
        record.get("business_id"), "business_id"
    )
    for error in (review_id_error, business_id_error):
        if error:
            errors.append(error)

    stars, stars_error = _review_stars(record.get("stars"))
    if stars_error:
        errors.append(stars_error)

    review_date, date_error = _review_date(record.get("date"))
    if date_error:
        errors.append(date_error)

    if errors:
        return ValidationResult(record=None, errors=tuple(errors))

    assert review_id is not None
    assert business_id is not None
    assert stars is not None
    assert review_date is not None
    return ValidationResult(
        record=ReviewRecord(
            review_id=review_id,
            business_id=business_id,
            stars=stars,
            date=review_date,
        )
    )
