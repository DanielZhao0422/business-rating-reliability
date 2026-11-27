from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import pytest

from rating_reliability.powerbi_config import (
    PowerBIConfigError,
    RelationshipSpec,
    load_powerbi_export_spec,
    validate_relationship_contract,
)
from rating_reliability.powerbi_export import _reconcile
from rating_reliability.powerbi_pseudonymization import (
    PseudonymizationError,
    normalize_seller_id,
    pseudonymize_seller,
    pseudonymize_sellers,
    secret_from_environment,
    validate_secret,
)
from rating_reliability.powerbi_validation import (
    PowerBIValidationError,
    validate_privacy,
    validate_relationships,
    validate_table_rows,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "config" / "powerbi_export_spec.toml"
STRONG_SECRET = b"d9A!7vQ2#fL8xR4@pT6mK1zN5sW3cY0u"


def test_export_contract_loads_exact_model() -> None:
    spec = load_powerbi_export_spec(SPEC)
    assert len(spec.tables) == 15
    assert len(spec.relationships) == 6
    assert len({table.file for table in spec.tables}) == 15
    assert spec.table_by_name["dim_seller"].primary_key == ("seller_key",)


def test_export_contract_rejects_unsafe_file(tmp_path: Path) -> None:
    text = SPEC.read_text(encoding="utf-8").replace(
        'file = "dim_method.csv"', 'file = "../dim_method.csv"', 1
    )
    path = tmp_path / "unsafe.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PowerBIConfigError, match="unsafe export filename"):
        load_powerbi_export_spec(path)


def test_export_contract_rejects_duplicate_table(tmp_path: Path) -> None:
    text = SPEC.read_text(encoding="utf-8").replace(
        'name = "dim_review_volume"', 'name = "dim_method"', 1
    )
    path = tmp_path / "duplicate.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PowerBIConfigError, match="duplicate export table"):
        load_powerbi_export_spec(path)


@pytest.mark.parametrize(
    "value",
    [None, "", "short", "changeme" * 8, "a" * 40],
)
def test_secret_validation_rejects_unsafe_values(value: str | None) -> None:
    with pytest.raises(PseudonymizationError):
        validate_secret(value)


def test_missing_environment_secret_fails_without_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POWERBI_SELLER_KEY_SECRET", raising=False)
    with pytest.raises(PseudonymizationError, match="is not configured"):
        secret_from_environment()


def test_hmac_pseudonyms_are_stable_normalized_and_keyed() -> None:
    first = pseudonymize_seller(" Seller-A ", STRONG_SECRET)
    second = pseudonymize_seller("seller-a", STRONG_SECRET)
    other = pseudonymize_seller("seller-a", b"X7!bP9@dR2#vL4$qT6%yN8^mK1&zC3*w")
    assert first == second
    assert first != other
    assert len(first) == 32
    assert normalize_seller_id(" Seller-A ") == "seller-a"
    assert "seller-a" not in first


def test_pseudonym_mapping_contains_no_reverse_mapping() -> None:
    result = pseudonymize_sellers(["seller-a", "seller-b"], STRONG_SECRET)
    assert set(result) == {"seller-a", "seller-b"}
    assert len(set(result.values())) == 2
    assert not (set(result.values()) & set(result))


def test_schema_rejects_duplicate_and_null_primary_keys() -> None:
    spec = load_powerbi_export_spec(SPEC)
    table = spec.table_by_name["dim_method"]
    row = OrderedDict(
        method_key="raw",
        method_label="Raw",
        method_order=1,
        method_description="Arithmetic mean",
        color_hex="#000000",
    )
    with pytest.raises(PowerBIValidationError, match="duplicate primary key"):
        validate_table_rows(table, [row, row])
    row["method_key"] = None
    with pytest.raises(PowerBIValidationError, match="unexpectedly null"):
        validate_table_rows(table, [row])


def _minimal_relationship_tables() -> dict[str, list[dict[str, object]]]:
    spec = load_powerbi_export_spec(SPEC)
    tables: dict[str, list[dict[str, object]]] = {
        table.name: [] for table in spec.tables
    }
    tables["dim_method"] = [{"method_key": "raw"}]
    tables["dim_review_volume"] = [{"volume_group_key": "1-4"}]
    tables["dim_scenario"] = [{"scenario_key": "s1"}]
    tables["dim_seller"] = [{"seller_key": "abc", "volume_group_key": "1-4"}]
    tables["fact_seller_estimate"] = [{"seller_key": "abc", "method_key": "raw"}]
    tables["fact_seller_evaluation"] = [{"seller_key": "abc"}]
    tables["fact_seller_operational"] = [{"seller_key": "abc"}]
    tables["fact_sensitivity_scenario"] = [{"scenario_key": "s1"}]
    return tables


def test_relationship_validation_accepts_approved_six() -> None:
    spec = load_powerbi_export_spec(SPEC)
    reports = validate_relationships(spec, _minimal_relationship_tables())
    assert len(reports) == 6
    assert all(report["passed"] for report in reports)
    assert all(
        report["active"]
        and report["cardinality"] == "many_to_one"
        and report["cross_filter"] == "single"
        and report["filter_direction"] == "to_table_to_from_table"
        for report in reports
    )


@pytest.mark.parametrize(
    ("cardinality", "filter_direction", "error"),
    [
        ("one_to_one", "to_table_to_from_table", "require Both"),
        ("many_to_many", "to_table_to_from_table", "unsupported.*cardinality"),
        ("many_to_one", "from_table_to_to_table", "filter direction"),
    ],
)
def test_config_rejects_unsupported_relationship(
    tmp_path: Path, cardinality: str, filter_direction: str, error: str
) -> None:
    text = (
        SPEC.read_text(encoding="utf-8")
        .replace('cardinality = "many_to_one"', f'cardinality = "{cardinality}"', 1)
        .replace(
            'filter_direction = "to_table_to_from_table"',
            f'filter_direction = "{filter_direction}"',
            1,
        )
    )
    path = tmp_path / "relationships.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PowerBIConfigError, match=error):
        load_powerbi_export_spec(path)


def test_one_to_one_both_requires_unique_child_keys() -> None:
    spec = load_powerbi_export_spec(SPEC)
    relationships = tuple(
        replace(
            rel, cardinality="one_to_one", cross_filter="both", filter_direction="both"
        )
        if rel.from_table == "fact_seller_evaluation"
        else rel
        for rel in spec.relationships
    )
    tables = _minimal_relationship_tables()
    both_spec = replace(spec, relationships=relationships)
    assert any(
        report["cross_filter"] == "both"
        for report in validate_relationships(both_spec, tables)
    )
    tables["fact_seller_evaluation"] *= 2
    with pytest.raises(PowerBIValidationError, match="not one-to-one"):
        validate_relationships(both_spec, tables)


@pytest.mark.parametrize("cycle", [True, False])
def test_relationship_contract_rejects_competing_paths(cycle: bool) -> None:
    ab = RelationshipSpec(
        "b", "key", "a", "key", "many_to_one", "single", "to_table_to_from_table"
    )
    bc = replace(ab, from_table="c", to_table="b")
    third = (
        replace(ab, from_table="a", to_table="c")
        if cycle
        else replace(
            bc,
            to_table="a",
            cardinality="one_to_one",
            cross_filter="both",
            filter_direction="both",
        )
    )
    with pytest.raises(PowerBIConfigError, match="cycle or ambiguous"):
        validate_relationship_contract((ab, bc, third))


def test_many_side_cardinality_does_not_relax_evaluation_grain() -> None:
    spec = load_powerbi_export_spec(SPEC)
    tables = _minimal_relationship_tables()
    seller = tables["fact_seller_evaluation"][0]
    tables["fact_seller_evaluation"].append(seller.copy())
    assert all(report["passed"] for report in validate_relationships(spec, tables))
    schema = spec.table_by_name["fact_seller_evaluation"]
    values = {"text": "value", "integer": 1, "decimal": 1.0, "boolean": False}
    row = dict(
        zip(schema.columns, (values[kind] for kind in schema.types), strict=True)
    )
    with pytest.raises(PowerBIValidationError, match="duplicate primary key"):
        validate_table_rows(schema, [row, row])
    tables["dim_seller"] *= 2
    with pytest.raises(PowerBIValidationError, match="parent.*not unique"):
        validate_relationships(spec, tables)


def test_relationship_validation_rejects_orphan() -> None:
    spec = load_powerbi_export_spec(SPEC)
    tables = _minimal_relationship_tables()
    tables["fact_seller_evaluation"][0]["seller_key"] = "orphan"
    with pytest.raises(PowerBIValidationError, match="orphan"):
        validate_relationships(spec, tables)


def test_relationship_checks_preserve_reconciliation_inputs(tmp_path: Path) -> None:
    spec = load_powerbi_export_spec(SPEC)
    tables = _minimal_relationship_tables()
    tables["fact_seller_evaluation"][0].update(
        raw_absolute_error=0.75, adjusted_absolute_error=0.5
    )
    tables["fact_ranking_summary"] = [{"spearman": 1.0}]
    evaluation = {"raw_mae": 0.75, "adjusted_mae": 0.5}
    operational = {"orders": 3, "sellers": 1, "evaluable": 2, "late": 1}
    reconciliation = {
        "evaluation": evaluation,
        "calibration": {"coverage": 1.0},
        "operational": operational,
    }
    sources = {
        "model_comparison.json": {
            "predictive_metrics": {
                "raw": {"seller_equal_mae": 0.75},
                "adjusted": {"seller_equal_mae": 0.5},
            }
        },
        "ranking_comparison.json": {"method_comparison": {"spearman_correlation": 1}},
        "calibration_summary.json": {
            "primary": {"overall": {"empirical_coverage": 1.0}}
        },
        "population_and_missingness.json": {
            "future_operational_cohort": {
                "orders": 3,
                "sellers": 1,
                "evaluable_lateness_orders": 2,
                "observed_late_orders": 1,
            }
        },
    }
    for name, data in sources.items():
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    expected = _reconcile(tables, reconciliation, tmp_path, tmp_path)
    validate_relationships(spec, tables)
    result = _reconcile(tables, reconciliation, tmp_path, tmp_path)
    assert result == expected
    assert result["export_recomputed"] == evaluation
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_privacy_validation_rejects_source_identifier_value() -> None:
    spec = load_powerbi_export_spec(SPEC)
    tables = {table.name: [] for table in spec.tables}
    tables["dim_seller"] = [
        {
            "seller_key": "raw-source-id",
            "seller_label": "Seller A",
            "historical_review_count": 1,
            "volume_group_key": "1-4",
            "has_evaluation_rating": True,
            "has_forward_operational_order": False,
        }
    ]
    with pytest.raises(PowerBIValidationError, match="source seller identifier"):
        validate_privacy(spec, tables, source_seller_ids=["raw-source-id"])
