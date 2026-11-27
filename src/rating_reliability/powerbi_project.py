"""Build a local Power BI project backed by the validated, immutable CSV export."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from shutil import copy2

from rating_reliability.artifacts import sha256_file
from rating_reliability.powerbi_config import (
    PowerBIExportSpec,
    TableSpec,
    load_powerbi_export_spec,
)
from rating_reliability.powerbi_export import _fingerprints, _reconcile
from rating_reliability.powerbi_measures import report_measures
from rating_reliability.powerbi_pages import (
    AMBER,
    CANVAS,
    MUTED,
    NAVY,
    STEEL,
    TEAL,
    Page,
    Visual,
    report_pages,
)
from rating_reliability.powerbi_validation import (
    validate_privacy,
    validate_relationships,
    validate_table_rows,
)

PROJECT = "RatingReliability"
SCHEMAS = "https://developer.microsoft.com/json-schemas/fabric/item"
PBIP_SCHEMA = (
    "https://developer.microsoft.com/json-schemas/fabric/pbip/"
    "pbipProperties/1.0.0/schema.json"
)
REPORT_SCHEMA = "3.1.0"
PAGE_SCHEMA = "2.0.0"
VISUAL_SCHEMA = "2.5.0"
THEME = "RatingReliability-Presentation.json"
M_TYPES = {
    "text": "type text",
    "integer": "Int64.Type",
    "decimal": "type number",
    "date": "type date",
    "boolean": "type logical",
}
MODEL_TYPES = {
    "text": "string",
    "integer": "int64",
    "decimal": "double",
    "date": "dateTime",
    "boolean": "boolean",
}
SORT_BY = {
    "DimMethod": ("method_label", "method_order"),
    "DimReviewVolume": ("display_label", "sort_order"),
    "DimScenario": ("scenario_label", "sort_order"),
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def model_name(name: str) -> str:
    return "".join(word.title() for word in name.split("_"))


def _m_text(text: str) -> str:
    return '"' + text.replace("#(", "#(#)(").replace('"', '""') + '"'


def m_partition(table: TableSpec) -> str:
    names = ", ".join(map(_m_text, table.columns))
    types = ", ".join(
        "{" + _m_text(column) + ", " + M_TYPES[kind] + "}"
        for column, kind in zip(table.columns, table.types, strict=True)
    )
    return f"""let
    Expected = {{{names}}},
    Csv = Csv.Document(File.Contents(ExportRoot & "\\" & {_m_text(table.file)}),
        [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true]),
    Checked = if Table.ColumnNames(Headers) = Expected then Headers
        else error "CSV schema differs from the approved export contract",
    Blanks = Table.ReplaceValue(Checked, "", null, Replacer.ReplaceValue, Expected),
    Typed = Table.TransformColumnTypes(Blanks, {{{types}}}, "en-US")
in
    Typed"""


def model_definition(spec: PowerBIExportSpec, export_root: Path) -> dict:
    tables = []
    for table in spec.tables:
        name = model_name(table.name)
        columns = []
        for column, kind in zip(table.columns, table.types, strict=True):
            item = {
                "name": column,
                "sourceColumn": column,
                "dataType": MODEL_TYPES[kind],
                "summarizeBy": "none",
                "isNullable": column in table.nullable,
            }
            if column == "seller_key":
                item["isHidden"] = True
            if kind == "date":
                item["formatString"] = "yyyy-MM-dd"
            if SORT_BY.get(name, (None, None))[0] == column:
                item["sortByColumn"] = SORT_BY[name][1]
            columns.append(item)
        node = {
            "name": name,
            "description": table.grain.replace("_", " "),
            "columns": columns,
            "partitions": [
                {
                    "name": name,
                    "mode": "import",
                    "source": {
                        "type": "m",
                        "expression": m_partition(table).splitlines(),
                    },
                }
            ],
        }
        if name == "FactStudySummary":
            node["measures"] = report_measures()
        tables.append(node)
    relationships = [
        {
            "name": _id(f"{r.from_table}.{r.from_column}.{r.to_table}"),
            "fromTable": model_name(r.from_table),
            "fromColumn": r.from_column,
            "toTable": model_name(r.to_table),
            "toColumn": r.to_column,
            "fromCardinality": "many",
            "toCardinality": "one",
            "isActive": True,
            "crossFilteringBehavior": "oneDirection",
        }
        for r in spec.relationships
    ]
    return {
        "name": PROJECT,
        "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US",
            "sourceQueryCulture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "expressions": [
                {
                    "name": "ExportRoot",
                    "kind": "m",
                    "expression": _m_text(str(export_root))
                    + ' meta [IsParameterQuery=true, Type="Text", '
                    "IsParameterQueryRequired=true]",
                }
            ],
            "annotations": [
                {
                    "name": "PBI_QueryOrder",
                    "value": json.dumps(["ExportRoot", *(t["name"] for t in tables)]),
                },
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "0"},
            ],
            "tables": tables,
            "relationships": relationships,
        },
    }


def _id(text: str) -> str:
    return sha256(text.encode()).hexdigest()[:20]


def _schema(kind: str, version: str) -> str:
    return f"{SCHEMAS}/report/definition/{kind}/{version}/schema.json"


def _literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _string(value: str) -> dict:
    return _literal("'" + value.replace("'", "''") + "'")


def _color(value: str) -> dict:
    return {"solid": {"color": _string(value)}}


def _object(**properties: object) -> list[dict]:
    return [{"properties": properties}]


DISPLAY_NAMES = {
    "display_label": "Historical reviews",
    "seller_label": "Seller",
    "order_status": "Fulfillment status",
    "method_label": "Rating method",
    "scenario_family": "Sensitivity family",
    "scenario_label": "Sensitivity scenario",
    "top_k": "Top K",
    "Volume Raw MAE": "Raw average",
    "Volume Adjusted MAE": "Adjusted rating",
    "Selected Historical Review Count": "Historical reviews",
    "Mean Absolute Rating Adjustment": "Absolute adjustment (stars)",
    "Raw Historical Rating": "Raw rating",
    "Adjusted Historical Rating": "Adjusted rating",
    "Expected Rating Interval Lower": "95% lower",
    "Expected Rating Interval Upper": "95% upper",
    "Volume Predictive Coverage": "Observed coverage",
    "Volume Nominal Coverage": "Nominal coverage",
    "Operational Historical Rating": "Historical rating (stars)",
    "Selected Late Delivery Rate": "Late-delivery rate",
    "Selected Evaluable Deliveries": "Evaluable deliveries",
    "Top-K Minimum Overlap": "Minimum",
    "Top-K Maximum Overlap": "Maximum",
    "Top-K Fractional Overlap": "Expected",
    "Top-K Feasible Overlap": "Feasible range",
    "Raw Average Rank": "Raw rank",
    "Adjusted Average Rank": "Adjusted rank",
    "Signed Rank Movement": "Adjusted − raw rank",
    "Other Prior Strengths": "Other strengths",
    "Selected Prior Strength Improvement": "Selected strength",
    "Lateness Spearman": "Spearman ρ",
    "Lateness CI Lower": "95% CI lower",
    "Lateness CI Upper": "95% CI upper",
    "Lateness Association Sellers": "Sellers",
    "Scenario Protocol Status": "Protocol status",
    "Scenario Raw MAE": "Raw MAE",
    "Scenario Adjusted MAE": "Adjusted MAE",
    "Scenario Improvement": "MAE reduction",
    "Scenario Sellers": "Sellers",
}


def _projection(binding: str) -> dict:
    table, dot, column = binding.partition(".")
    if not dot:
        table, column = "FactStudySummary", binding
    return {
        "field": {
            "Column" if dot else "Measure": {
                "Expression": {"SourceRef": {"Entity": table}},
                "Property": column,
            }
        },
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": column,
        **({"displayName": DISPLAY_NAMES[column]} if column in DISPLAY_NAMES else {}),
    }


def _reset_control(container: dict) -> dict:
    default = {"id": "default"}
    return {
        **container,
        "visual": {
            "visualType": "actionButton",
            "objects": {
                "text": [
                    *_object(show=_literal("true")),
                    {
                        "selector": default,
                        "properties": {
                            "text": _string("Reset filters"),
                            "fontSize": _literal("11D"),
                            "fontColor": _color(NAVY),
                            "horizontalAlignment": _string("center"),
                        },
                    },
                ],
                "icon": [
                    *_object(show=_literal("false")),
                    {"selector": default, "properties": {"placement": _string("left")}},
                ],
                "fill": [
                    *_object(show=_literal("true")),
                    {
                        "selector": default,
                        "properties": {"fillColor": _color("#E5EDF4")},
                    },
                ],
            },
            "visualContainerObjects": {
                "visualLink": _object(
                    show=_literal("true"), type=_string("ClearAllSlicers")
                ),
                "general": _object(altText=_string("Clear the slicers on this page")),
            },
        },
    }


def _pbir_visual(visual: Visual, name: str, box: dict, order: int) -> dict:
    container = {
        "$schema": _schema("visualContainer", VISUAL_SCHEMA),
        "name": name,
        "position": {**box, "z": order * 1000, "tabOrder": order * 1000},
    }
    if visual.kind == "pageNavigator":
        return {
            **container,
            "visual": {
                "visualType": "pageNavigator",
                "visualContainerObjects": {
                    "general": _object(
                        altText=_string("Navigate the five report pages")
                    )
                },
            },
        }
    if visual.kind == "actionButton":
        return _reset_control(container)
    if visual.kind == "textbox":
        size = {
            "identity": "12pt",
            "title": "25pt",
            "subtitle": "13pt",
            "footnote": "11pt",
        }.get(visual.key, "12pt")
        paragraphs = visual.title.splitlines()
        properties = {
            "paragraphs": [
                {
                    "textRuns": [
                        {
                            "value": paragraph,
                            "textStyle": {
                                "fontFamily": "Segoe UI",
                                "fontSize": "15pt"
                                if index == 0 and len(paragraphs) > 1
                                else size,
                                "fontWeight": "bold"
                                if visual.key in ("title", "identity")
                                or (index == 0 and len(paragraphs) > 1)
                                else "normal",
                                "color": NAVY
                                if index == 0
                                and visual.key not in ("footnote", "subtitle")
                                else MUTED,
                            },
                        }
                    ]
                }
                for index, paragraph in enumerate(paragraphs)
            ]
        }
        return {
            **container,
            "visual": {
                "visualType": "textbox",
                "objects": {"general": [{"properties": properties}]},
                "visualContainerObjects": {
                    "background": _object(show=_literal("false"))
                    if len(paragraphs) == 1
                    else _object(
                        show=_literal("true"),
                        color=_color("#FFFFFF"),
                        transparency=_literal("0D"),
                    ),
                },
            },
        }
    state = {
        role: {"projections": [_projection(binding) for binding in bindings]}
        for role, bindings in visual.roles.items()
    }
    query: dict = {"queryState": state}
    if visual.sort:
        query["sortDefinition"] = {
            "sort": [
                {"field": _projection(visual.sort)["field"], "direction": "Ascending"}
            ]
        }
    fmt = {
        "title": _object(
            show=_literal("true"),
            text=_string(visual.title),
            fontColor=_color(NAVY),
            fontSize=_literal("12D"),
        ),
        "general": _object(altText=_string(visual.title)),
        "background": _object(
            show=_literal("true"),
            color=_color("#FFFFFF"),
            transparency=_literal("0D"),
        ),
    }
    if "Tooltips" in visual.roles:
        fmt["visualTooltip"] = _object(show=_literal("true"))
    objects = {
        axis: [
            {
                "properties": {
                    "start": _literal(f"{lower}D"),
                    "end": _literal(f"{upper}D"),
                }
            }
        ]
        for axis, lower, upper in visual.axes
    }
    if visual.kind == "slicer":
        fmt["title"] = _object(show=_literal("false"))
        objects["data"] = _object(mode=_string("Dropdown"))
        objects["header"] = _object(show=_literal("true"), text=_string(visual.title))
        if visual.key in (
            "DimMethod.method_label",
            "DimSeller.seller_label",
            "DimScenario.scenario_label",
        ):
            objects["selection"] = _object(singleSelect=_literal("true"))
    if visual.kind == "card":
        hero = visual.key in ("Relative MAE Improvement", "Raw Sellers in Ties")
        compact = box["height"] < 85 or visual.key == "Seller Evidence Display"
        objects["categoryLabels"] = _object(show=_literal("false"))
        objects["labels"] = _object(
            labelDisplayUnits=_literal("1D"),
            fontSize=_literal("43D" if hero else "18D" if compact else "26D"),
            color=_color(NAVY),
        )
        if hero:
            fmt["background"] = _object(
                show=_literal("true"),
                color=_color("#DBEEEB"),
                transparency=_literal("0D"),
            )
    if visual.kind in ("lineChart", "clusteredBarChart", "clusteredColumnChart"):
        for axis in ("categoryAxis", "valueAxis"):
            properties = objects.setdefault(axis, _object())[0]["properties"]
            properties["showAxisTitle"] = _literal("false")
            properties["fontSize"] = _literal("11D")
        if visual.key == "volume":
            objects["valueAxis"][0]["properties"].update(
                showAxisTitle=_literal("true"),
                titleText=_string("Seller-equal MAE (stars)"),
            )
        objects["legend"] = _object(
            show=_literal("true" if len(visual.roles["Y"]) > 1 else "false"),
            showTitle=_literal("false"),
            position=_string("Top"),
        )
    if visual.colors:
        objects["dataPoint"] = [
            {
                "selector": {"metadata": _projection(binding)["queryRef"]},
                "properties": {"fill": _color(color)},
            }
            for binding, color in zip(visual.roles["Y"], visual.colors, strict=True)
        ]
        if len(visual.colors) == 1:
            objects["dataPoint"] = _object(defaultColor=_color(visual.colors[0]))
    if visual.key == "calibration":
        objects["lineStyles"] = [
            {
                "selector": {"metadata": "FactStudySummary.Volume Nominal Coverage"},
                "properties": {"lineStyle": _string("dashed")},
            }
        ]
    if visual.labels:
        objects["labels"] = _object(show=_literal("true"), fontSize=_literal("10D"))
        if visual.key == "calibration":
            objects["labels"].append(
                {
                    "selector": {
                        "metadata": "FactStudySummary.Volume Nominal Coverage"
                    },
                    "properties": {"showSeries": _literal("false")},
                }
            )
    if visual.kind == "scatterChart":
        objects["markers"] = _object(
            transparency=_literal("43D"), borderShow=_literal("false")
        )
        objects["bubbles"] = _object(
            bubbleSize=_literal("-10L"), preventOverflow=_literal("true")
        )
        objects["dataPoint"] = _object(defaultColor=_color(TEAL))
        for axis in ("categoryAxis", "valueAxis"):
            properties = objects[axis][0]["properties"]
            properties["showAxisTitle"] = _literal("true")
            properties["fontSize"] = _literal("11D")
        if visual.log_x:
            objects["categoryAxis"][0]["properties"]["logAxisScale"] = _literal("true")
        if visual.key == "ranks":
            objects["y1AxisReferenceLine"] = [
                {
                    "selector": {"id": "0"},
                    "properties": {
                        "show": _literal("true"),
                        "value": _literal("0D"),
                        "displayName": _string("Equal ranks"),
                        "style": _string("dashed"),
                        "lineColor": _color(MUTED),
                    },
                }
            ]
    if visual.kind == "tableEx":
        objects["columnHeaders"] = _object(
            autoSizeColumnWidth=_literal("true"),
            fontColor=_color(NAVY),
            backColor=_color("#E9F0F6"),
            fontSize=_literal("11D"),
        )
        objects["values"] = _object(
            fontColorPrimary=_color(NAVY), fontSize=_literal("11D")
        )
        objects["total"] = _object(totals=_literal("false"))
        if visual.key == "overlap":
            objects["grid"] = _object(rowPadding=_literal("2D"))
    return {
        **container,
        "visual": {
            "visualType": visual.kind,
            "query": query,
            "objects": objects,
            "visualContainerObjects": fmt,
        },
    }


def page_definition(page: Page) -> tuple[dict, list[dict]]:
    name = _id(page.name)
    placed = [
        (
            Visual("identity", "textbox", "BUSINESS RATING RELIABILITY"),
            dict(x=36, y=17, width=288, height=34),
        ),
        (
            Visual("navigation", "pageNavigator", "Report pages"),
            dict(x=332, y=12, width=708, height=42),
        ),
        (
            Visual("title", "textbox", page.title),
            dict(x=36, y=54, width=1208, height=55),
        ),
        (
            Visual("subtitle", "textbox", page.subtitle),
            dict(x=36, y=109, width=1208, height=38),
        ),
    ]
    if any(v.kind == "slicer" for _, row in page.rows for v in row):
        placed.insert(
            2,
            (
                Visual("reset", "actionButton", "Reset filters"),
                dict(x=1060, y=12, width=184, height=42),
            ),
        )
    y = 152
    for height, row in page.rows:
        weights = {
            "volume": (8, 4),
            "paired": (7, 5),
            "ranks": (6.5, 5.5),
            "association": (7, 5),
            "FactRankingSummary.top_k": (2, 2, 2),
            "DimScenario.scenario_family": (2.5, 3, 6.5),
            "Relative MAE Improvement": (4, 2.5, 2.5, 3),
            "Eligible Ratings": (2, 2.8, 2.8, 2, 2),
            "MAE Improvement": (3, 4, 3, 3),
        }.get(row[0].key, (1,) * len(row))
        widths = [
            (1208 - (len(row) - 1) * 16) * weight / sum(weights) for weight in weights
        ]
        x = 36
        for item, width in zip(row, widths, strict=True):
            first_height = (height - 10) / 2 if item.below else height
            placed.append(
                (
                    item,
                    dict(x=x, y=y, width=width, height=first_height),
                )
            )
            if item.below:
                placed.append(
                    (
                        item.below,
                        dict(
                            x=x,
                            y=y + first_height + 10,
                            width=width,
                            height=first_height,
                        ),
                    )
                )
            x += width + 16
        y += height + 10
    placed.append(
        (
            Visual("footnote", "textbox", page.footnote),
            dict(x=36, y=666, width=1208, height=46),
        )
    )
    keys = {item.key: _id(f"{page.name}/{item.key}") for item, _ in placed}
    data_visuals = [item for item, _ in placed if item.roles]
    interactions = [
        {
            "source": keys[source.key],
            "target": keys[target.key],
            "type": "DataFilter" if target.key in source.targets else "NoFilter",
        }
        for source in data_visuals
        for target in data_visuals
        if source.key != target.key
    ]
    metadata = {
        "$schema": _schema("page", PAGE_SCHEMA),
        "name": name,
        "displayName": page.name,
        "height": 720,
        "width": 1280,
        "displayOption": "FitToPage",
        "objects": {
            "background": [
                {
                    "properties": {
                        "color": _color(CANVAS),
                        "transparency": _literal("0D"),
                    }
                }
            ]
        },
        "visualInteractions": interactions,
    }
    return metadata, [
        _pbir_visual(item, keys[item.key], box, index)
        for index, (item, box) in enumerate(placed, 1)
    ]


def report_theme() -> dict:
    return {
        "name": "Business Rating Reliability",
        "dataColors": [
            STEEL,
            TEAL,
            NAVY,
            AMBER,
            "#7656A6",
            "#98A2B3",
        ],
        "background": "#FFFFFF",
        "foreground": NAVY,
        "tableAccent": TEAL,
        "good": "#2E7D32",
        "neutral": AMBER,
        "bad": "#B54747",
        "textClasses": {
            "callout": {
                "fontFace": "Segoe UI Semibold",
                "fontSize": 26,
                "color": NAVY,
            },
            "title": {
                "fontFace": "Segoe UI Semibold",
                "fontSize": 12,
                "color": NAVY,
            },
            "label": {"fontFace": "Segoe UI", "fontSize": 11, "color": MUTED},
        },
    }


def _write_project(
    folder: Path, spec: PowerBIExportSpec, root: Path, model: dict | None = None
) -> None:
    report_name = f"{PROJECT}.Report"
    model_name_ = f"{PROJECT}.SemanticModel"
    _write(
        folder / model_name_ / "definition.pbism",
        {
            "$schema": (
                f"{SCHEMAS}/semanticModel/definitionProperties/1.0.0/schema.json"
            ),
            "version": "4.0",
            "settings": {},
        },
    )
    _write(
        folder / model_name_ / "model.bim",
        model if model is not None else model_definition(spec, root),
    )
    report = folder / report_name
    _write(
        report / "definition.pbir",
        {
            "$schema": f"{SCHEMAS}/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{model_name_}"}},
        },
    )
    versions = {"visual": VISUAL_SCHEMA, "report": REPORT_SCHEMA, "page": PAGE_SCHEMA}
    base = {
        "name": "CY25SU12",
        "reportVersionAtImport": versions,
        "type": "SharedResources",
    }
    theme = {
        "name": THEME,
        "reportVersionAtImport": versions,
        "type": "RegisteredResources",
    }
    _write(
        report / "definition/report.json",
        {
            "$schema": _schema("report", REPORT_SCHEMA),
            "themeCollection": {"baseTheme": base, "customTheme": theme},
            "resourcePackages": [
                {
                    "name": "SharedResources",
                    "type": "SharedResources",
                    "items": [
                        {
                            "name": base["name"],
                            "path": f"BaseThemes/{base['name']}.json",
                            "type": "BaseTheme",
                        }
                    ],
                },
                {
                    "name": "RegisteredResources",
                    "type": "RegisteredResources",
                    "items": [{"name": THEME, "path": THEME, "type": "CustomTheme"}],
                },
            ],
        },
    )
    _write(
        report / "definition/version.json",
        {"$schema": _schema("versionMetadata", "1.0.0"), "version": "2.0.0"},
    )
    _write(report / "StaticResources/RegisteredResources" / THEME, report_theme())
    pages = []
    for page in report_pages():
        meta, visuals = page_definition(page)
        pages.append(meta["name"])
        path = report / "definition/pages" / meta["name"]
        _write(path / "page.json", meta)
        for visual in visuals:
            _write(path / "visuals" / visual["name"] / "visual.json", visual)
    _write(
        report / "definition/pages/pages.json",
        {
            "$schema": _schema("pagesMetadata", "1.0.0"),
            "pageOrder": pages,
            "activePageName": pages[0],
        },
    )
    _write(
        folder / f"{PROJECT}.pbip",
        {
            "$schema": PBIP_SCHEMA,
            "version": "1.0",
            "artifacts": [{"report": {"path": report_name}}],
        },
    )


def audit_export(
    repo: Path, spec: PowerBIExportSpec, package: Path | None = None
) -> dict:
    """Recheck the published package and compare its locked source fingerprints."""
    if package is None:
        package = repo / spec.output_directory
    manifest_path = package / "metadata/export_manifest.json"
    manifest = _read(manifest_path)
    source = _read(package / "metadata/reproducibility.json")["source_fingerprints"]
    b, c = (
        repo / "reports/local" / name for name in ("statistical_analysis", "phase3c")
    )
    expected = {
        "database": sha256_file(repo / "data/processed/olist_analytics.duckdb"),
        "statistical_protocol": sha256_file(repo / "config/statistical_protocol.toml"),
        "export_spec": sha256_file(repo / "config/powerbi_export_spec.toml"),
        "phase3b": _fingerprints(b),
        "phase3c": _fingerprints(c),
    }
    if source != expected:
        raise ValueError("The validated export source fingerprints have changed")
    entries = manifest["exported_files"]
    files = {t.file for t in spec.tables}
    if (
        manifest["spec_version"] != spec.spec_version
        or not manifest["validation_passed"]
        or manifest["contains_source_identifiers"]
        or manifest["relationships"] != len(spec.relationships)
        or files != set(entries)
        or files != {p.name for p in (package / "exports").iterdir()}
    ):
        raise ValueError("The export manifest does not match the current contract")
    parsers = {
        "text": str,
        "integer": int,
        "decimal": float,
        "date": date.fromisoformat,
        "boolean": {"true": True, "false": False}.__getitem__,
    }
    tables = {}
    schema_reports = []
    for table in spec.tables:
        path = package / "exports" / table.file
        with path.open(encoding=spec.encoding, newline="") as stream:
            reader = csv.reader(stream, strict=True)
            if tuple(next(reader)) != table.columns:
                raise ValueError(f"Unexpected CSV headers for {table.file}")
            rows = [
                {
                    key: parsers[kind](cell) if cell else None
                    for key, kind, cell in zip(
                        table.columns, table.types, row, strict=True
                    )
                }
                for row in reader
            ]
        if entries[table.file] != {
            "table": table.name,
            "columns": list(table.columns),
            "grain": table.grain,
            "rows": len(rows),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }:
            raise ValueError(f"Manifest entry differs for {table.file}")
        tables[table.name] = rows
        schema_reports.append(validate_table_rows(table, rows))
    validation = package / "validation"
    relationships = validate_relationships(spec, tables)
    checks = {
        "schema_validation": {"passed": True, "tables": schema_reports},
        "relationship_validation": {"passed": True, "relationships": relationships},
        # The files are byte-identical to those covered by the published raw-ID scan.
        "privacy_validation": validate_privacy(spec, tables, source_seller_ids=[]),
    }
    reconciliation = _read(validation / "statistical_reconciliation.json")
    checks["statistical_reconciliation"] = _reconcile(
        tables, reconciliation["source_to_export"], b, c
    )
    if any(
        value != _read(validation / f"{name}.json") for name, value in checks.items()
    ):
        raise ValueError("A validation report differs from the audited package")
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "export_spec_version": spec.spec_version,
        "source_fingerprints": expected,
        "export_rows": {key: len(rows) for key, rows in tables.items()},
        "checkpoints": _checkpoints(tables, reconciliation),
        "relationships": relationships,
        "desktop_runtime": "pending",
    }


def _checkpoints(tables: dict, reconciliation: dict) -> dict:
    study = {
        row["metric_key"]: row["metric_value"] for row in tables["fact_study_summary"]
    }
    calibration = next(
        row
        for row in tables["fact_calibration"]
        if row["calibration_dimension"] == "overall"
        and row["calibration_group"] == "all_evaluation_sellers"
    )
    evaluation = tables["fact_seller_evaluation"]
    operations = tables["fact_seller_operational"]
    rank = tables["fact_ranking_summary"][0]
    observed = sum(row["predictive_covered"] for row in evaluation) / len(evaluation)
    orders, evaluable, late = (
        sum(row[name] for row in operations)
        for name in (
            "forward_order_count",
            "evaluable_delivery_count",
            "late_delivery_count",
        )
    )
    if (
        not math.isclose(observed, calibration["coverage"], rel_tol=0, abs_tol=1e-12)
        or any(
            reconciliation["source_to_export"]["operational"][key] != value
            for key, value in (
                ("orders", orders),
                ("evaluable", evaluable),
                ("late", late),
            )
        )
        or len(evaluation) != study["evaluation_sellers"]
        or any(
            not math.isclose(study[key], val, rel_tol=0, abs_tol=1e-12)
            for key, val in reconciliation["export_recomputed"].items()
        )
    ):
        raise ValueError("CSV checkpoints do not match the locked results")
    return {
        **study,
        "historical_sellers": len(tables["dim_seller"]),
        "observed_coverage": observed,
        "nominal_coverage": calibration["nominal_coverage"],
        "forward_orders": orders,
        "evaluable_deliveries": evaluable,
        "late_deliveries": late,
        "late_rate": late / evaluable,
        **{
            key: rank[key]
            for key in ("spearman", "kendall_tau_b", "mean_absolute_rank_change")
        },
    }


def _presentation_model(original: Path, spec: PowerBIExportSpec, root: Path) -> dict:
    """Preserve Desktop model metadata while rebinding the entire import package."""
    saved = _read(original / f"{PROJECT}.SemanticModel/model.bim")
    model = saved["model"]
    contract = model_definition(spec, root)["model"]
    for actual, expected in zip(model["tables"], contract["tables"], strict=True):
        if (
            actual["name"] != expected["name"]
            or [c["name"] for c in actual["columns"]]
            != [c["name"] for c in expected["columns"]]
            or actual["partitions"][0]["source"] != expected["partitions"][0]["source"]
            or len(actual["partitions"]) != 1
        ):
            raise ValueError(
                f"Original import differs from the contract: {actual['name']}"
            )
    for actual, expected in zip(
        model["relationships"], contract["relationships"], strict=True
    ):
        if any(
            actual.get(
                key,
                expected[key]
                if key
                in (
                    "fromCardinality",
                    "toCardinality",
                    "crossFilteringBehavior",
                    "isActive",
                )
                else None,
            )
            != expected[key]
            for key in expected.keys() - {"name"}
        ):
            raise ValueError(
                "Saved model relationships differ from the export contract"
            )
    if [expression["name"] for expression in model["expressions"]] != ["ExportRoot"]:
        raise ValueError("Expected a shared ExportRoot model parameter")
    model["expressions"][0]["expression"] = contract["expressions"][0]["expression"]
    summary = next(t for t in model["tables"] if t["name"] == "FactStudySummary")
    saved_measures = {measure["name"]: measure for measure in summary["measures"]}
    for measure in report_measures():
        saved_measures.setdefault(measure["name"], {}).update(measure)
    summary["measures"] = list(saved_measures.values())
    return saved


def build_project(
    repo: Path,
    name: str = "desktop",
    *,
    presentation: bool = False,
    output_parent: Path | None = None,
) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
        raise ValueError("The project folder name must be a single safe path component")
    spec = load_powerbi_export_spec(repo / "config/powerbi_export_spec.toml")
    package = repo / spec.output_directory
    output = (output_parent or package) / name
    if output.exists():
        raise FileExistsError(f"Project folder already exists: {output}")
    if any(
        r.cardinality != "many_to_one" or r.cross_filter != "single"
        for r in spec.relationships
    ):
        raise ValueError(
            "The report requires the approved active 1:* Single relationships"
        )
    original = package / "desktop"
    source = (
        package.with_name("powerbi-availability-correction")
        if presentation
        else package
    )
    audit = audit_export(repo, spec, source)
    root = (source / "exports").resolve()
    model = _presentation_model(original, spec, root) if presentation else None
    output.mkdir()
    _write_project(output, spec, root, model)
    if model is not None:
        model_folder = f"{PROJECT}.SemanticModel"
        for name in (
            f"{model_folder}/definition.pbism",
            f"{model_folder}/diagramLayout.json",
        ):
            copy2(original / name, output / name)
    _write(
        output / "build_metadata.json",
        {
            **audit,
            "built_at_utc": datetime.now(UTC).isoformat(),
            "export_root": str(root),
            "baseline_cache_copied": False,
            "project_format": {
                "pbip": "1.0",
                "pbir": "4.0",
                "semantic_model": "TMSL",
                "theme": THEME,
            },
            "pages": [p.name for p in report_pages()],
            "measures": len(report_measures()),
        },
    )
    dax = output / "Validation.dax"
    dax.write_text(_validation_dax(), encoding="utf-8")
    return output / f"{PROJECT}.pbip"


def _validation_dax() -> str:
    fixed = (
        "Eligible Ratings",
        "Rated Sellers",
        "Historical Sellers",
        "Evaluation Sellers",
        "Raw MAE",
        "Adjusted MAE",
        "MAE Improvement",
        "Relative MAE Improvement",
        "Raw RMSE",
        "Adjusted RMSE",
        "Observed Predictive Coverage",
        "Nominal Predictive Coverage",
        "Forward Orders",
        "Evaluable Deliveries",
        "Late Deliveries",
        "Late Delivery Rate",
        "Spearman",
        "Kendall Tau-b",
        "Mean Absolute Rank Movement",
        "Raw Sellers in Ties",
        "Largest Raw Rating Tie",
        "Historical Observations",
        "Subsequent Observations",
        "Historical Reference Mean",
        "Selected Prior Strength",
    )
    rows = ",\n    ".join(f'"{key}", [{key}]' for key in fixed)
    return f"""EVALUATE
ROW (
    {rows}
)

EVALUATE
CALCULATETABLE (
    ROW ("Fixed raw MAE", [Raw MAE], "Fixed adjusted MAE", [Adjusted MAE],
        "Subgroup raw MAE", [Subgroup Raw MAE],
        "Subgroup adjusted MAE", [Subgroup Adjusted MAE],
        "Subgroup sellers", [Subgroup Evaluation Sellers]),
    DimReviewVolume[volume_group_key] = "1-4"
)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument(
        "--name", default="desktop", help="New folder under the local export package"
    )
    parser.add_argument(
        "--presentation",
        action="store_true",
        help="Preserve the original Desktop model and use the corrected import package",
    )
    parser.add_argument(
        "--output-parent", type=Path, help="Existing destination parent"
    )
    args = parser.parse_args()
    name = (
        "final-presentation"
        if args.presentation and args.name == "desktop"
        else args.name
    )
    print(
        build_project(
            args.repo.resolve(),
            name,
            presentation=args.presentation,
            output_parent=args.output_parent,
        )
    )


if __name__ == "__main__":
    main()
