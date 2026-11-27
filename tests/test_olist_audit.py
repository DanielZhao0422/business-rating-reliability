"""Synthetic regression tests for the Olist suitability audit."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from rating_reliability.olist_audit import OlistAuditError, main, run_audit


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)


def _synthetic_olist(tmp_path: Path) -> Path:
    data = tmp_path / "olist"
    data.mkdir()
    orders = [
        [
            order_id,
            "customer-secret",
            "delivered",
            purchase,
            purchase,
            purchase,
            delivered,
            estimated,
        ]
        for order_id, purchase, delivered, estimated in (
            ("o1", "2017-01-01", "2017-01-05", "2017-01-06"),
            ("o2", "2017-02-01", "2017-02-08", "2017-02-06"),
            ("o3", "2017-03-01", "2017-03-05", "2017-03-06"),
            ("o4", "2017-04-01", "2017-04-05", "2017-04-06"),
            ("o5", "2018-01-01", "2018-01-05", "2018-01-06"),
            ("o6", "2018-02-01", "2018-02-05", "2018-02-06"),
            ("o7", "2018-08-01", "2018-08-05", "2018-08-06"),
        )
    ]
    _write_csv(
        data / "olist_orders_dataset.csv",
        [
            "order_id",
            "customer_id",
            "order_status",
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
        orders,
    )
    _write_csv(
        data / "olist_order_items_dataset.csv",
        [
            "order_id",
            "order_item_id",
            "product_id",
            "seller_id",
            "shipping_limit_date",
            "price",
            "freight_value",
        ],
        [
            ["o1", 1, "p1", "s1", "2017-01-02", 10, 2],
            ["o1", 2, "p2", "s1", "2017-01-02", 20, 3],
            ["o2", 1, "p1", "s1", "2017-02-02", 10, 2],
            ["o2", 2, "p2", "s2", "2017-02-02", 20, 3],
            ["o3", 1, "p1", "s2", "2017-03-02", 10, 2],
            ["o5", 1, "p1", "s2", "2018-01-02", 10, 2],
            ["o6", 1, "p1", "s1", "2018-02-02", 10, 2],
            ["o7", 1, "p1", "s2", "2018-08-02", 10, 2],
        ],
    )
    _write_csv(
        data / "olist_order_reviews_dataset.csv",
        [
            "review_id",
            "order_id",
            "review_score",
            "review_comment_title",
            "review_comment_message",
            "review_creation_date",
            "review_answer_timestamp",
        ],
        [
            ["r1", "o1", 5, "title", "SECRET REVIEW", "2017-01-06", "2017-01-07"],
            ["r2", "o2", 1, "", "", "2017-02-09", "2017-02-10"],
            ["rd", "o3", 4, "", "duplicate", "2017-03-06", "2017-03-07"],
            ["rd", "o3", 4, "", "duplicate", "2017-03-06", "2017-03-07"],
            ["shared", "o5", 2, "", "", "2018-01-06", "2018-01-07"],
            ["shared", "o6", 3, "", "", "2018-02-06", "2018-02-07"],
            ["r7", "o7", 3, "", "", "2018-08-06", "2018-08-07"],
        ],
    )
    _write_csv(
        data / "olist_sellers_dataset.csv",
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
        [["s1", 1, "City", "SP"], ["s2", 2, "Town", "RJ"]],
    )
    return data


def test_audit_applies_conservative_attribution_and_duplicate_rules(
    tmp_path: Path,
) -> None:
    data = _synthetic_olist(tmp_path)
    output = tmp_path / "audit"

    result = run_audit(data_directory=data, output_directory=output)

    assert result["seller_attribution"]["usable_rating_observations"] == 2
    assert result["seller_attribution"]["sellers_with_usable_ratings"] == 2
    assert result["seller_attribution"]["multi_seller_reviewed_orders_excluded"] == 1
    assert result["review_quality"]["exact_duplicate_excess_rows"] == 1
    assert result["review_quality"]["duplicate_review_id_excess_rows"] == 2
    assert result["review_quality"]["conflicting_duplicate_id_excess_rows"] == 1
    assert result["review_quality"]["review_ids_linked_to_multiple_orders"] == 1
    assert result["relational_quality"]["orders_without_items"] == 1


def test_audit_outputs_are_complete_private_and_reproducible(tmp_path: Path) -> None:
    data = _synthetic_olist(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    run_audit(data_directory=data, output_directory=first)
    run_audit(data_directory=data, output_directory=second)

    for name in (
        "audit_summary.json",
        "data_quality_summary.json",
        "seller_review_distribution.csv",
        "temporal_feasibility.csv",
    ):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in first.iterdir()
        if path.suffix in {".json", ".csv", ".md"}
    )
    assert "SECRET REVIEW" not in combined
    assert "customer-secret" not in combined
    assert json.loads((first / "audit_summary.json").read_text())["recommendation"]
    assert all((first / name).stat().st_size > 1_000 for name in first.glob("*.png"))


def test_audit_refuses_overwrite_and_missing_inputs(tmp_path: Path) -> None:
    data = _synthetic_olist(tmp_path)
    output = tmp_path / "audit"
    run_audit(data_directory=data, output_directory=output)

    with pytest.raises(OlistAuditError, match="--overwrite"):
        run_audit(data_directory=data, output_directory=output)
    run_audit(data_directory=data, output_directory=output, overwrite=True)

    with pytest.raises(OlistAuditError, match="required input"):
        run_audit(data_directory=tmp_path / "missing", output_directory=tmp_path / "x")


def test_audit_cli_reports_success_and_ordinary_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _synthetic_olist(tmp_path)
    output = tmp_path / "cli"

    assert main(["--data-dir", str(data), "--output-dir", str(output)]) == 0
    captured = capsys.readouterr()
    assert "CONDITIONALLY APPROVE OLIST" in captured.out

    assert main(["--data-dir", str(tmp_path / "none")]) == 1
    captured = capsys.readouterr()
    assert "required input" in captured.err
    assert "Traceback" not in captured.err
