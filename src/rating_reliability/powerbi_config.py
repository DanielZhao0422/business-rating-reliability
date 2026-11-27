"""Validated configuration contract for local Power BI exports."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePath


class PowerBIConfigError(ValueError):
    """Raised when the Power BI export contract is invalid."""


SUPPORTED_TYPES = frozenset({"text", "integer", "decimal", "boolean", "date"})
SUPPORTED_CARDINALITIES = frozenset({"many_to_one", "one_to_one"})


@dataclass(frozen=True)
class TableSpec:
    """One configured CSV table and its exact schema."""

    name: str
    file: str
    grain: str
    primary_key: tuple[str, ...]
    source: str
    columns: tuple[str, ...]
    types: tuple[str, ...]
    nullable: frozenset[str]


@dataclass(frozen=True)
class RelationshipSpec:
    """One active relationship, stored with the parent in the to_table field."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str
    cross_filter: str
    filter_direction: str


@dataclass(frozen=True)
class PowerBIExportSpec:
    """Complete Phase 4B export contract."""

    spec_version: str
    phase: str
    status: str
    encoding: str
    delimiter: str
    line_ending: str
    null_token: str
    output_directory: str
    primary_cutoff: date
    privacy: dict[str, object]
    refresh: dict[str, object]
    tables: tuple[TableSpec, ...]
    relationships: tuple[RelationshipSpec, ...]

    @property
    def table_by_name(self) -> dict[str, TableSpec]:
        return {table.name: table for table in self.tables}


def _required(mapping: dict[str, object], key: str, context: str) -> object:
    if key not in mapping:
        raise PowerBIConfigError(f"{context} is missing required field {key!r}")
    return mapping[key]


def _safe_file_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PowerBIConfigError("table file must be a nonempty string")
    path = PurePath(value)
    if path.is_absolute() or len(path.parts) != 1 or ".." in path.parts:
        raise PowerBIConfigError(f"unsafe export filename: {value!r}")
    if path.suffix.lower() != ".csv":
        raise PowerBIConfigError(f"export filename must end in .csv: {value!r}")
    return value


def _safe_relative_directory(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PowerBIConfigError("output_directory must be a nonempty string")
    path = PurePath(value)
    if path.is_absolute() or ".." in path.parts:
        raise PowerBIConfigError("output_directory must be a safe relative path")
    return value


def validate_relationship_contract(relationships: tuple[RelationshipSpec, ...]) -> None:
    """Check Power BI settings and require a single active path per table pair."""
    edges: dict[str, list[tuple[str, int]]] = {}
    for index, relationship in enumerate(relationships):
        child, parent = relationship.from_table, relationship.to_table
        if child == parent:
            raise PowerBIConfigError(f"self-relationship on {parent}")
        if relationship.cardinality not in SUPPORTED_CARDINALITIES:
            raise PowerBIConfigError("unsupported relationship cardinality")
        if (
            relationship.cardinality == "one_to_one"
            and relationship.cross_filter != "both"
        ):
            raise PowerBIConfigError(
                "Power BI one-to-one relationships require Both filtering"
            )
        direction = {"single": "to_table_to_from_table", "both": "both"}
        if direction.get(relationship.cross_filter) != relationship.filter_direction:
            raise PowerBIConfigError(
                "invalid relationship cross-filter or filter direction"
            )
        edges.setdefault(parent, []).append((child, index))
        if relationship.cross_filter == "both":
            edges.setdefault(child, []).append((parent, index))

    for source in edges:
        visited = {source}
        pending = [(source, -1)]
        while pending:
            current, incoming = pending.pop()
            for target, edge in edges.get(current, ()):
                # Traversing the same bidirectional relationship back adds no path.
                if edge == incoming:
                    continue
                if target in visited:
                    raise PowerBIConfigError(
                        "relationship cycle or ambiguous filter paths "
                        f"from {source} to {target}"
                    )
                visited.add(target)
                pending.append((target, edge))


def load_powerbi_export_spec(path: Path) -> PowerBIExportSpec:
    """Load and fully validate the TOML export contract."""

    try:
        with path.open("rb") as source:
            config = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PowerBIConfigError(
            f"cannot read Power BI export spec: {error}"
        ) from error

    raw_tables = _required(config, "tables", "export spec")
    raw_schemas = _required(config, "schemas", "export spec")
    raw_relationships = _required(config, "relationships", "export spec")
    if not isinstance(raw_tables, list) or not isinstance(raw_schemas, dict):
        raise PowerBIConfigError("tables and schemas must be TOML table collections")
    if not isinstance(raw_relationships, list):
        raise PowerBIConfigError("relationships must be a TOML table array")

    tables: list[TableSpec] = []
    names: set[str] = set()
    files: set[str] = set()
    for index, raw in enumerate(raw_tables):
        context = f"tables[{index}]"
        if not isinstance(raw, dict):
            raise PowerBIConfigError(f"{context} must be a table")
        name = _required(raw, "name", context)
        if not isinstance(name, str) or not name:
            raise PowerBIConfigError(f"{context}.name must be nonempty text")
        if name in names:
            raise PowerBIConfigError(f"duplicate export table name: {name}")
        names.add(name)
        file_name = _safe_file_name(_required(raw, "file", context))
        if file_name in files:
            raise PowerBIConfigError(f"duplicate export filename: {file_name}")
        files.add(file_name)
        schema = raw_schemas.get(name)
        if not isinstance(schema, dict):
            raise PowerBIConfigError(f"missing schema for export table {name}")
        columns = tuple(schema.get("columns", ()))
        types = tuple(schema.get("types", ()))
        nullable = frozenset(schema.get("nullable", ()))
        if not columns or len(columns) != len(types):
            raise PowerBIConfigError(f"{name} columns and types must have equal length")
        if len(set(columns)) != len(columns) or any(
            not isinstance(column, str) or not column for column in columns
        ):
            raise PowerBIConfigError(f"{name} has duplicate or invalid columns")
        invalid_types = set(types) - SUPPORTED_TYPES
        if invalid_types:
            raise PowerBIConfigError(
                f"{name} has unsupported column types: {sorted(invalid_types)}"
            )
        if not nullable <= set(columns):
            raise PowerBIConfigError(f"{name} nullable fields must be schema columns")
        primary_key = tuple(_required(raw, "primary_key", context))
        if not primary_key or not set(primary_key) <= set(columns):
            raise PowerBIConfigError(f"{name} primary key is absent from its schema")
        tables.append(
            TableSpec(
                name=name,
                file=file_name,
                grain=str(_required(raw, "grain", context)),
                primary_key=primary_key,
                source=str(_required(raw, "source", context)),
                columns=columns,
                types=types,
                nullable=nullable,
            )
        )
    if set(raw_schemas) != names:
        extra = sorted(set(raw_schemas) - names)
        raise PowerBIConfigError(f"schemas do not match table definitions: {extra}")

    table_map = {table.name: table for table in tables}
    relationships: list[RelationshipSpec] = []
    seen_relationships: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_relationships):
        context = f"relationships[{index}]"
        if not isinstance(raw, dict):
            raise PowerBIConfigError(f"{context} must be a table")
        relationship = RelationshipSpec(
            from_table=str(_required(raw, "from_table", context)),
            from_column=str(_required(raw, "from_column", context)),
            to_table=str(_required(raw, "to_table", context)),
            to_column=str(_required(raw, "to_column", context)),
            cardinality=str(_required(raw, "cardinality", context)),
            cross_filter=str(_required(raw, "cross_filter", context)),
            filter_direction=str(_required(raw, "filter_direction", context)),
        )
        key = (
            relationship.from_table,
            relationship.from_column,
            relationship.to_table,
            relationship.to_column,
        )
        if key in seen_relationships:
            raise PowerBIConfigError(f"duplicate relationship: {key}")
        seen_relationships.add(key)
        if (
            relationship.from_table not in table_map
            or relationship.to_table not in table_map
        ):
            raise PowerBIConfigError(f"{context} references an unknown table")
        child = table_map[relationship.from_table]
        if relationship.from_column not in child.columns:
            raise PowerBIConfigError(f"{context} references an unknown child column")
        parent = table_map[relationship.to_table]
        if parent.primary_key != (relationship.to_column,):
            raise PowerBIConfigError(f"{context} parent column is not a primary key")
        if (
            child.types[child.columns.index(relationship.from_column)]
            != parent.types[parent.columns.index(relationship.to_column)]
        ):
            raise PowerBIConfigError(f"{context} relationship column types must match")
        relationships.append(relationship)

    validate_relationship_contract(tuple(relationships))

    privacy = _required(config, "privacy", "export spec")
    refresh = _required(config, "refresh", "export spec")
    if not isinstance(privacy, dict) or not isinstance(refresh, dict):
        raise PowerBIConfigError("privacy and refresh must be TOML tables")
    if any(
        bool(privacy.get(key))
        for key in (
            "export_source_seller_id",
            "export_order_id",
            "export_review_id",
            "export_customer_id",
            "export_review_text",
        )
    ):
        raise PowerBIConfigError("privacy contract cannot enable restricted exports")

    return PowerBIExportSpec(
        spec_version=str(_required(config, "spec_version", "export spec")),
        phase=str(_required(config, "phase", "export spec")),
        status=str(_required(config, "status", "export spec")),
        encoding=str(_required(config, "encoding", "export spec")),
        delimiter=str(_required(config, "delimiter", "export spec")),
        line_ending=str(_required(config, "line_ending", "export spec")),
        null_token=str(_required(config, "null_token", "export spec")),
        output_directory=_safe_relative_directory(
            _required(config, "output_directory", "export spec")
        ),
        primary_cutoff=_required(config, "primary_cutoff", "export spec"),
        privacy=privacy,
        refresh=refresh,
        tables=tuple(tables),
        relationships=tuple(relationships),
    )
