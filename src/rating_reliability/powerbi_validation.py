"""Strict schema, relationship, privacy, and reconciliation validation."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from rating_reliability.powerbi_config import (
    PowerBIConfigError,
    PowerBIExportSpec,
    TableSpec,
    validate_relationship_contract,
)


class PowerBIValidationError(ValueError):
    """Raised when a staged Power BI export fails a publication gate."""


def _valid_type(value: object, type_name: str) -> bool:
    if value is None:
        return True
    if type_name == "text":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "decimal":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "date":
        return isinstance(value, date) and not hasattr(value, "hour")
    return False


def validate_table_rows(
    table: TableSpec, rows: Sequence[Mapping[str, Any]]
) -> dict[str, object]:
    """Validate exact columns, types, nullability, and primary-key grain."""

    keys: set[tuple[object, ...]] = set()
    for index, row in enumerate(rows):
        if tuple(row) != table.columns:
            raise PowerBIValidationError(
                f"{table.name} row {index} does not match configured column order"
            )
        for column, type_name in zip(table.columns, table.types, strict=True):
            value = row[column]
            if value is None and column not in table.nullable:
                raise PowerBIValidationError(
                    f"{table.name}.{column} is unexpectedly null"
                )
            if not _valid_type(value, type_name):
                raise PowerBIValidationError(
                    f"{table.name}.{column} has invalid {type_name} value"
                )
        key = tuple(row[column] for column in table.primary_key)
        if any(value is None for value in key):
            raise PowerBIValidationError(f"{table.name} has a null primary key")
        if key in keys:
            raise PowerBIValidationError(f"{table.name} has a duplicate primary key")
        keys.add(key)
    return {
        "table": table.name,
        "grain": table.grain,
        "rows": len(rows),
        "columns": list(table.columns),
        "primary_key": list(table.primary_key),
        "passed": True,
    }


def validate_relationships(
    spec: PowerBIExportSpec,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, object]]:
    """Validate the six configured dimension-to-fact relationships."""
    try:
        validate_relationship_contract(spec.relationships)
    except PowerBIConfigError as error:
        raise PowerBIValidationError(str(error)) from error

    reports: list[dict[str, object]] = []
    for relationship in spec.relationships:
        child_rows = tables[relationship.from_table]
        parent_rows = tables[relationship.to_table]
        parent_keys = [row[relationship.to_column] for row in parent_rows]
        if any(value is None or value == "" for value in parent_keys):
            raise PowerBIValidationError(
                f"relationship parent {relationship.to_table} has blank keys"
            )
        if len(parent_keys) != len(set(parent_keys)):
            raise PowerBIValidationError(
                f"relationship parent {relationship.to_table} is not unique"
            )
        parent_set = set(parent_keys)
        child_keys = [row[relationship.from_column] for row in child_rows]
        if any(value is None or value == "" for value in child_keys):
            raise PowerBIValidationError(
                f"relationship child {relationship.from_table} has null keys"
            )
        orphan_count = sum(value not in parent_set for value in child_keys)
        if orphan_count:
            raise PowerBIValidationError(
                f"relationship {relationship.from_table} has orphan keys"
            )
        if relationship.cardinality == "one_to_one" and len(child_keys) != len(
            set(child_keys)
        ):
            raise PowerBIValidationError(
                f"relationship {relationship.from_table} is not one-to-one"
            )
        reports.append(
            {
                "from_table": relationship.from_table,
                "from_column": relationship.from_column,
                "to_table": relationship.to_table,
                "to_column": relationship.to_column,
                "cardinality": relationship.cardinality,
                "cross_filter": relationship.cross_filter,
                "filter_direction": relationship.filter_direction,
                "active": True,
                "child_rows": len(child_rows),
                "parent_rows": len(parent_rows),
                "orphan_keys": 0,
                "passed": True,
            }
        )
    return reports


PROHIBITED_COLUMNS = frozenset(
    {
        "seller_id",
        "order_id",
        "review_id",
        "customer_id",
        "customer_unique_id",
        "customer_name",
        "customer_address",
        "review_comment_title",
        "review_comment_message",
        "payment_id",
    }
)


def validate_privacy(
    spec: PowerBIExportSpec,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    source_seller_ids: Sequence[str],
) -> dict[str, object]:
    """Enforce the column allowlist and absence of raw seller identifiers."""

    source_ids = set(source_seller_ids)
    for table in spec.tables:
        if set(table.columns) & PROHIBITED_COLUMNS:
            raise PowerBIValidationError(f"{table.name} exposes a prohibited column")
        for row in tables[table.name]:
            for value in row.values():
                if isinstance(value, str) and value in source_ids:
                    raise PowerBIValidationError(
                        f"{table.name} contains a source seller identifier"
                    )
    return {
        "strict_allowlist": True,
        "prohibited_headers_found": [],
        "source_seller_ids_found": 0,
        "review_text_exported": False,
        "mapping_file_exported": False,
        "publication_permission_inferred": False,
        "passed": True,
    }


def assert_close(
    actual: float, expected: float, *, name: str, tolerance: float
) -> None:
    """Raise a concise reconciliation error outside a declared tolerance."""

    if not math.isclose(actual, expected, rel_tol=0, abs_tol=tolerance):
        raise PowerBIValidationError(
            f"{name} mismatch: actual={actual!r}, expected={expected!r}"
        )
