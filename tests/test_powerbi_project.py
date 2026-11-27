"""Static checks for the native Power BI project and its source bindings."""

import json
import re
from itertools import combinations
from pathlib import Path

import pytest

from rating_reliability.powerbi_config import load_powerbi_export_spec
from rating_reliability.powerbi_measures import report_measures
from rating_reliability.powerbi_pages import report_pages
from rating_reliability.powerbi_project import (
    M_TYPES,
    MODEL_TYPES,
    PROJECT,
    _write_project,
    build_project,
    model_definition,
    model_name,
    page_definition,
    report_theme,
)
from rating_reliability.powerbi_validation import PROHIBITED_COLUMNS

SPEC = Path(__file__).parents[1] / "config/powerbi_export_spec.toml"


def test_import_partitions_follow_approved_contract(tmp_path: Path) -> None:
    spec = load_powerbi_export_spec(SPEC)
    model = model_definition(spec, tmp_path)["model"]
    assert [query["name"] for query in model["expressions"]] == ["ExportRoot"]
    assert str(tmp_path) in model["expressions"][0]["expression"]
    for contract, table in zip(spec.tables, model["tables"], strict=True):
        assert table["name"] == model_name(contract.name)
        columns = {c["name"]: c for c in table["columns"]}
        assert tuple(columns) == contract.columns
        assert not set(columns) & PROHIBITED_COLUMNS
        partition = table["partitions"][0]
        assert partition["mode"] == "import"
        expression = "\n".join(partition["source"]["expression"])
        assert '"en-US"' in expression
        assert expression.count("File.Contents(") == 1
        assert "File.Contents(ExportRoot" in expression
        assert expression.count(contract.file) == 1
        assert "Table.ColumnNames(Headers) = Expected" in expression
        for name, kind in zip(contract.columns, contract.types, strict=True):
            assert columns[name]["dataType"] == MODEL_TYPES[kind]
            assert columns[name]["isNullable"] == (name in contract.nullable)
            assert f'{{"{name}", {M_TYPES[kind]}}}' in expression
        assert "POWERBI_SELLER_KEY_SECRET" not in expression
    for rel, actual in zip(spec.relationships, model["relationships"], strict=True):
        assert actual["fromTable"] == model_name(rel.from_table)
        assert actual["toTable"] == model_name(rel.to_table)
        assert actual["fromColumn"] == rel.from_column
        assert actual["toColumn"] == rel.to_column
        assert actual["isActive"]
        assert actual["crossFilteringBehavior"] == "oneDirection"
        assert (actual["fromCardinality"], actual["toCardinality"]) == ("many", "one")


def test_measure_references_resolve_and_primary_kpis_remove_filters() -> None:
    spec = load_powerbi_export_spec(SPEC)
    columns = {model_name(table.name): table.columns for table in spec.tables}
    definitions = report_measures()
    measures = {m["name"]: m["expression"] for m in definitions}
    assert len(measures) == len(definitions) == 102
    for expression in measures.values():
        for table, col in re.findall(r"(\w+)\[([^\]]+)\]", expression):
            assert col in columns[table]
        for dependency in re.findall(r"(?<!\w)\[([\w -]+)\]", expression):
            assert dependency in measures.keys() | {"SellersAtRating"}
    for name in ("Raw MAE", "Adjusted MAE", "Relative MAE Improvement"):
        assert "REMOVEFILTERS ()" in measures[name]
        assert "FactStudySummary[metric_value]" in measures[name]
        assert not re.search(r"\d+\.\d+", measures[name])
    assert "AVERAGEX" in measures["Subgroup Raw MAE"]
    assert "DIVIDE" in measures["Selected Late Delivery Rate"]


def test_page_files_and_visual_fields_are_consistent(tmp_path: Path) -> None:
    spec = load_powerbi_export_spec(SPEC)
    columns = {model_name(t.name): t.columns for t in spec.tables}
    measures = {m["name"] for m in report_measures()}
    _write_project(tmp_path, spec, tmp_path / "exports")
    entry = json.loads((tmp_path / f"{PROJECT}.pbip").read_bytes())
    report = tmp_path / entry["artifacts"][0]["report"]["path"]
    assert (report / "definition.pbir").is_file()
    assert report_theme()["dataColors"][:2] == ["#4472A0", "#10867F"]
    assert [p.name for p in report_pages()] == [
        "Overview",
        "Ratings",
        "Rankings",
        "Validation",
        "Operations",
    ]
    for page in report_pages():
        metadata, visuals = page_definition(page)
        ids = {v["name"] for v in visuals}
        assert len(ids) == len(visuals)
        assert any(v["visual"]["visualType"] == "pageNavigator" for v in visuals)
        folder = report / "definition/pages" / metadata["name"]
        assert len(list(folder.glob("visuals/*/visual.json"))) == len(visuals)
        for visual in visuals:
            box = visual["position"]
            assert 0 <= box["x"] < box["x"] + box["width"] <= 1280
            assert 0 <= box["y"] < box["y"] + box["height"] <= 720
            query = visual["visual"].get("query", {})
            for role in query.get("queryState", {}).values():
                for projection in role["projections"]:
                    ((kind, ref),) = projection["field"].items()
                    name = ref["Property"]
                    entity = ref["Expression"]["SourceRef"]["Entity"]
                    assert name in (measures if kind == "Measure" else columns[entity])
                    assert name != "seller_key"
        for interaction in metadata["visualInteractions"]:
            assert {interaction["source"], interaction["target"]} <= ids
    counted = len(list((report / "definition/pages").glob("*/page.json")))
    assert counted == 5


def test_build_refuses_to_replace_existing_report(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / SPEC.name).write_bytes(SPEC.read_bytes())
    output = tmp_path / "reports/local/powerbi/desktop"
    output.mkdir(parents=True)
    existing = output / "existing.pbix"
    existing.write_bytes(b"sentinel")
    with pytest.raises(FileExistsError, match="already exists"):
        build_project(tmp_path)
    assert existing.read_bytes() == b"sentinel"


def test_rankings_overlap_displays_zero_ranges_as_text() -> None:
    rankings = next(page for page in report_pages() if page.name == "Rankings")
    _, visuals = page_definition(rankings)
    (table,) = (
        visual["visual"]
        for visual in visuals
        if visual["visual"]["visualType"] == "tableEx"
    )
    query = table["query"]
    category, bounds, expected = query["queryState"]["Values"]["projections"]
    assert bounds["field"]["Measure"]["Property"] == "Top-K Feasible Overlap"
    assert expected["field"]["Measure"]["Property"] == "Top-K Fractional Overlap"
    assert category["field"] == {
        "Column": {
            "Expression": {"SourceRef": {"Entity": "FactRankingSummary"}},
            "Property": "top_k",
        }
    }
    assert query["sortDefinition"]["sort"] == [
        {"field": category["field"], "direction": "Ascending"}
    ]


@pytest.mark.parametrize("page", report_pages(), ids=lambda page: page.name)
def test_report_layout_and_controls(page) -> None:
    metadata, visuals = page_definition(page)
    assert (metadata["width"], metadata["height"]) == (1280, 720)
    assert (metadata, visuals) == page_definition(page)
    assert sum(v["visual"]["visualType"] == "pageNavigator" for v in visuals) == 1
    for first, second in combinations(visuals, 2):
        a, b = first["position"], second["position"]
        assert (
            a["x"] + a["width"] <= b["x"] + 1e-8
            or b["x"] + b["width"] <= a["x"] + 1e-8
            or a["y"] + a["height"] <= b["y"]
            or b["y"] + b["height"] <= a["y"]
        ), (page.name, first["name"], second["name"])
    slicers = [v for v in visuals if v["visual"]["visualType"] == "slicer"]
    resets = [
        v["visual"] for v in visuals if v["visual"]["visualType"] == "actionButton"
    ]
    assert len(resets) == bool(slicers)
    for visual in resets:
        action = visual["visualContainerObjects"]["visualLink"][0]["properties"]
        assert action["type"]["expr"]["Literal"]["Value"] == "'ClearAllSlicers'"
    for visual in slicers:
        assert visual["position"]["height"] >= 74
        header = visual["visual"]["objects"]["header"][0]["properties"]
        assert header["show"]["expr"]["Literal"]["Value"] == "true"
        assert re.fullmatch(r"'[A-Za-z ]+'", header["text"]["expr"]["Literal"]["Value"])
    for item in visuals:
        body = item["visual"]
        if "Tooltips" in body.get("query", {}).get("queryState", {}):
            assert body["visualContainerObjects"]["visualTooltip"]
