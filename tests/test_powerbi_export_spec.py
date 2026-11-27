"""Validate the Phase 4A Power BI export design contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

SPEC = Path(__file__).parents[1] / "config" / "powerbi_export_spec.toml"


def test_powerbi_export_spec_has_unique_tables_files_and_keys() -> None:
    with SPEC.open("rb") as source:
        config = tomllib.load(source)

    tables = config["tables"]
    assert config["status"] == "implemented_local_export_contract"
    assert config["phase"] == "4B-1"
    assert config["primary_cutoff"].isoformat() == "2018-01-01"
    assert len({table["name"] for table in tables}) == len(tables)
    assert len({table["file"] for table in tables}) == len(tables)
    assert all(table["primary_key"] for table in tables)
    assert all(table["grain"].startswith("one_row_per_") for table in tables)


def test_powerbi_relationships_are_single_direction_and_reference_keys() -> None:
    with SPEC.open("rb") as source:
        config = tomllib.load(source)

    tables = {table["name"]: table for table in config["tables"]}
    for relationship in config["relationships"]:
        assert relationship["cross_filter"] == "single"
        assert relationship["filter_direction"] == "to_table_to_from_table"
        assert relationship["from_table"] in tables
        assert relationship["to_table"] in tables
        assert (
            relationship["to_column"] in tables[relationship["to_table"]]["primary_key"]
        )


def test_powerbi_privacy_contract_excludes_source_identifiers() -> None:
    with SPEC.open("rb") as source:
        privacy = tomllib.load(source)["privacy"]

    assert privacy["real_data_outputs_git_ignored"] is True
    assert privacy["export_source_seller_id"] is False
    assert privacy["export_order_id"] is False
    assert privacy["export_review_id"] is False
    assert privacy["export_customer_id"] is False
    assert privacy["export_review_text"] is False
