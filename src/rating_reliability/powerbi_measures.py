"""Report measures for the approved Phase 4B semantic model."""

from __future__ import annotations


def report_measures() -> list[dict[str, str]]:
    measures: list[dict[str, str]] = []

    def add(name: str, expression: str, fmt: str = "0.000") -> None:
        measures.append({"name": name, "expression": expression, "formatString": fmt})

    def fixed(expression: str) -> str:
        return f"CALCULATE ({expression}, REMOVEFILTERS ())"

    def lookup(table: str, column: str, filters: str) -> str:
        return f"CALCULATE (MAX ({table}[{column}]), REMOVEFILTERS (), {filters})"

    for name, key, fmt in (
        ("Eligible Ratings", "eligible_ratings", "#,0"),
        ("Rated Sellers", "rated_sellers", "#,0"),
        ("Evaluation Sellers", "evaluation_sellers", "#,0"),
        ("Raw MAE", "raw_mae", "0.000"),
        ("Adjusted MAE", "adjusted_mae", "0.000"),
        ("MAE Improvement", "mae_improvement", "0.000"),
        ("Relative MAE Improvement", "relative_mae_improvement", "0.0%"),
        ("Bootstrap Lower", "bootstrap_lower", "0.000"),
        ("Bootstrap Upper", "bootstrap_upper", "0.000"),
        ("Selected Prior Strength", "selected_prior_strength", "0"),
        ("Historical Observations", "historical_ratings", "#,0"),
        ("Subsequent Observations", "evaluation_ratings", "#,0"),
    ):
        add(
            name,
            lookup(
                "FactStudySummary",
                "metric_value",
                f'FactStudySummary[metric_key] = "{key}"',
            ),
            fmt,
        )
    add("Historical Sellers", fixed("COUNTROWS (DimSeller)"), "#,0")
    add(
        "Historical Reference Mean",
        lookup(
            "FactSellerEstimate",
            "reference_mean",
            'FactSellerEstimate[method_key] = "adjusted"',
        ),
    )
    add("Selected Historical Sellers", "COUNTROWS (DimSeller)", "#,0")
    add(
        "Selected Historical Review Count",
        "SELECTEDVALUE (DimSeller[historical_review_count])",
        "#,0",
    )
    add(
        "Average Historical Review Count",
        "AVERAGE (DimSeller[historical_review_count])",
        "0.0",
    )
    add(
        "Median Historical Review Count",
        "MEDIAN (DimSeller[historical_review_count])",
        "0.0",
    )
    add(
        "Seller Marker Size",
        "MIN (10, SQRT ([Selected Historical Review Count]))",
        "0.0",
    )

    for method in ("raw", "adjusted"):
        label = method.capitalize()
        add(
            f"{label} Historical Rating",
            "CALCULATE (AVERAGE (FactSellerEstimate[estimate]), DimMethod[method_key] "
            f'= "{method}")',
        )
        for metric, col in (("MAE", "absolute_error"), ("RMSE", "squared_error")):
            table = "FactSellerEvaluation"
            avg = (
                f"AVERAGEX (VALUES ({table}[seller_key]), "
                f"CALCULATE (MAX ({table}[{method}_{col}])))"
            )
            add(
                f"Subgroup {label} {metric}",
                f"SQRT ({avg})" if metric == "RMSE" else avg,
            )
        add(f"{label} RMSE", fixed(f"[Subgroup {label} RMSE]"))
    add("Subgroup Evaluation Sellers", "COUNTROWS (FactSellerEvaluation)", "#,0")
    add("Subgroup MAE Improvement", "[Subgroup Raw MAE] - [Subgroup Adjusted MAE]")
    add(
        "Subgroup Relative MAE Improvement",
        "DIVIDE ([Subgroup MAE Improvement], [Subgroup Raw MAE])",
        "0.00%",
    )
    add("Visible Historical Rating", "AVERAGE (FactSellerEstimate[estimate])")
    add(
        "Operational Historical Rating",
        "IF (HASONEVALUE (DimMethod[method_key]), [Visible Historical Rating], "
        "[Adjusted Historical Rating])",
    )
    for method in ("raw", "adjusted"):
        add(
            f"{method.title()} Average Rank",
            "CALCULATE (SELECTEDVALUE (FactSellerEstimate[average_rank]), "
            f'DimMethod[method_key] = "{method}", DimSeller[has_evaluation_rating] = '
            "TRUE ())",
        )
    add(
        "Rating Reference Line",
        "IF (HASONEVALUE (DimSeller[seller_key]), [Raw Historical Rating])",
    )
    add(
        "Paired Bootstrap Interval",
        '"[" & FORMAT ([Bootstrap Lower], "0.000") & ", " & '
        'FORMAT ([Bootstrap Upper], "0.000") & "]"',
        "",
    )
    add(
        "Mean Absolute Rating Adjustment",
        """AVERAGEX (VALUES (DimSeller[seller_key]),
    CALCULATE (MAX (FactSellerEstimate[adjustment_absolute]),
        DimMethod[method_key] = "adjusted"))""",
    )
    for bound in ("lower", "upper"):
        add(
            f"Expected Rating Interval {bound.title()}",
            "IF (HASONEVALUE (DimSeller[seller_key]), CALCULATE "
            f"(SELECTEDVALUE (FactSellerEstimate[expected_rating_interval_{bound}]), "
            'DimMethod[method_key] = "adjusted"))',
        )
    add(
        "Seller Evidence Display",
        """VAR Seller = SELECTEDVALUE (DimSeller[seller_label])
RETURN
    IF (ISBLANK (Seller),
        "Select a seller or a dot to see its ratings and interval.",
        Seller & "  |  " & FORMAT ([Selected Historical Review Count], "#,0") &
        " reviews  |  Raw " & FORMAT ([Raw Historical Rating], "0.00") &
        " ★  |  Adjusted " & FORMAT ([Adjusted Historical Rating], "0.00") &
        " ★  |  95% interval " & FORMAT ([Expected Rating Interval Lower], "0.00") &
        "–" & FORMAT ([Expected Rating Interval Upper], "0.00") & " ★")""",
        "",
    )

    for name, column, fmt in (
        ("Spearman", "spearman", "0.00"),
        ("Kendall Tau-b", "kendall_tau_b", "0.00"),
        ("Mean Absolute Rank Movement", "mean_absolute_rank_change", "#,0"),
        ("Ranking Sellers", "seller_universe", "#,0"),
        ("Raw Boundary Tie Sellers", "boundary_tie_candidates", "#,0"),
        ("Strong Pair Ordering Support", "strong_ordering_support_90", "0.00%"),
    ):
        add(name, fixed(f"SELECTEDVALUE (FactRankingSummary[{column}])"), fmt)
    for name, col in (
        ("Top-K Minimum Overlap", "minimum_overlap_count"),
        ("Top-K Maximum Overlap", "maximum_overlap_count"),
        ("Top-K Fractional Overlap", "fractional_overlap_count"),
    ):
        add(
            name,
            f"IF (HASONEVALUE (FactRankingSummary[top_k]), "
            f"SELECTEDVALUE (FactRankingSummary[{col}]))",
            "0.00" if name == "Top-K Fractional Overlap" else "0",
        )
    add(
        "Top-K Feasible Overlap",
        "IF (HASONEVALUE (FactRankingSummary[top_k]), "
        'FORMAT ([Top-K Minimum Overlap], "0") & "–" & '
        'FORMAT ([Top-K Maximum Overlap], "0"))',
        "",
    )
    add(
        "Signed Rank Movement",
        "IF (NOT ISBLANK ([Raw Average Rank]) && "
        "NOT ISBLANK ([Adjusted Average Rank]), "
        "[Adjusted Average Rank] - [Raw Average Rank])",
        "0",
    )
    for k in (10, 25, 50):
        add(
            f"Posterior Top {k} Probability",
            "IF (HASONEVALUE (DimSeller[seller_key]), CALCULATE "
            f"(SELECTEDVALUE (FactSellerEstimate[top_{k}_probability]), "
            'DimMethod[method_key] = "adjusted"))',
            "0.0%",
        )
    for name, statistic in (
        (
            "Raw Sellers in Ties",
            "SUMX (FILTER (Distribution, [SellersAtRating] > 1), [SellersAtRating])",
        ),
        ("Largest Raw Rating Tie", "MAXX (Distribution, [SellersAtRating])"),
    ):
        add(
            name,
            f"""CALCULATE (
    VAR Distribution = GROUPBY (FactSellerEstimate, FactSellerEstimate[estimate],
        "SellersAtRating", COUNTX (CURRENTGROUP (), 1))
    RETURN {statistic},
    REMOVEFILTERS (), DimMethod[method_key] = "raw",
    DimSeller[has_evaluation_rating] = TRUE ())""",
            "#,0",
        )

    for name, col, fmt in (
        ("Observed Predictive Coverage", "coverage", "0.0%"),
        ("Nominal Predictive Coverage", "nominal_coverage", "0%"),
        ("Mean Predictive Interval Width", "mean_width", "0.000"),
    ):
        add(
            name,
            lookup(
                "FactCalibration",
                col,
                'FactCalibration[calibration_dimension] = "overall", '
                'FactCalibration[calibration_group] = "all_evaluation_sellers"',
            ),
            fmt,
        )
    for name, col in (
        ("Volume Predictive Coverage", "coverage"),
        ("Volume Nominal Coverage", "nominal_coverage"),
        ("Volume Mean Interval Width", "mean_width"),
        ("Volume Calibration Sellers", "seller_units"),
    ):
        fmt = (
            "0.0%"
            if "Coverage" in name
            else "#,0"
            if name.endswith("Sellers")
            else "0.000"
        )
        add(
            name,
            f"CALCULATE (SELECTEDVALUE (FactCalibration[{col}]), "
            'FactCalibration[calibration_dimension] = "historical_volume", '
            "TREATAS (VALUES (DimReviewVolume[volume_group_key]), "
            "FactCalibration[calibration_group]))",
            fmt,
        )
    for name, col in (
        ("Volume Raw MAE", "raw_mae"),
        ("Volume Adjusted MAE", "adjusted_mae"),
        ("Volume MAE Improvement", "mae_improvement"),
        ("Volume Evaluation Sellers", "seller_count"),
        ("Volume Historical Ratings", "historical_rating_count"),
        ("Volume Evaluation Ratings", "evaluation_rating_count"),
    ):
        add(
            name,
            f"CALCULATE (SELECTEDVALUE (FactEvaluationVolume[{col}]), "
            'FactEvaluationVolume[group_basis] = "historical", '
            "TREATAS (VALUES (DimReviewVolume[volume_group_key]), "
            "FactEvaluationVolume[volume_group_key]))",
            "#,0" if name.endswith(("Sellers", "Ratings")) else "0.000",
        )

    for name, col in (
        ("Forward Orders", "forward_order_count"),
        ("Evaluable Deliveries", "evaluable_delivery_count"),
        ("Late Deliveries", "late_delivery_count"),
    ):
        add(f"Selected {name}", f"SUM (FactSellerOperational[{col}])", "#,0")
        add(name, fixed(f"[Selected {name}]"), "#,0")
    add(
        "Selected Late Delivery Rate",
        "DIVIDE ([Selected Late Deliveries], [Selected Evaluable Deliveries])",
        "0.0%",
    )
    add("Late Delivery Rate", fixed("[Selected Late Delivery Rate]"), "0.0%")
    add(
        "Selected Operational Coverage",
        "DIVIDE ([Selected Evaluable Deliveries], [Selected Forward Orders])",
        "0.0%",
    )
    add("Operational Coverage", fixed("[Selected Operational Coverage]"), "0.0%")
    add("Status Orders", "SUM (FactOperationalStatus[order_count])", "#,0")
    add(
        "Status Proportion",
        "SELECTEDVALUE (FactOperationalStatus[order_proportion])",
        "0.0%",
    )
    add(
        "Quartile Late Rate",
        "SELECTEDVALUE (FactOperationalQuartile[late_delivery_rate])",
        "0.0%",
    )
    for method in ("raw", "adjusted"):
        add(
            f"{method.title()} Quartile Late Rate",
            "CALCULATE ([Quartile Late Rate], FactOperationalQuartile[method_key] = "
            f'"{method}")',
            "0.0%",
        )
    for name, col in (
        ("Lateness Spearman", "spearman"),
        ("Lateness CI Lower", "spearman_ci_lower"),
        ("Lateness CI Upper", "spearman_ci_upper"),
        ("Lateness Association Sellers", "seller_count"),
        ("Lateness Association Orders", "order_count"),
    ):
        add(
            name,
            f"CALCULATE (SELECTEDVALUE (FactOperationalAssociation[{col}]), "
            'FactOperationalAssociation[outcome_key] = "late_delivery_rate", '
            "TREATAS (VALUES (DimMethod[method_key]), "
            "FactOperationalAssociation[method_key]))",
            "#,0" if name.endswith(("Sellers", "Orders")) else "0.000",
        )
    for name, col, fmt in (
        ("Scenario Raw MAE", "raw_mae", "0.000"),
        ("Scenario Adjusted MAE", "adjusted_mae", "0.000"),
        ("Scenario Improvement", "mae_improvement", "0.000"),
        ("Scenario Relative Improvement", "relative_mae_improvement", "0.00%"),
        ("Scenario Sellers", "seller_count", "#,0"),
        ("Scenario Retention", "retained_proportion", "0.0%"),
        ("Scenario Bootstrap Lower", "bootstrap_lower", "0.000"),
        ("Scenario Bootstrap Upper", "bootstrap_upper", "0.000"),
    ):
        add(
            name,
            "IF (HASONEVALUE (DimScenario[scenario_key]), "
            f"SELECTEDVALUE (FactSensitivityScenario[{col}]))",
            fmt,
        )
    add(
        "Prior Sensitivity Improvement",
        "CALCULATE ([Scenario Improvement], "
        'KEEPFILTERS (DimScenario[scenario_family] = "prior_strength"))',
    )
    for name, comparator in (
        ("Other Prior Strengths", "<>"),
        ("Selected Prior Strength Improvement", "="),
    ):
        add(
            name,
            f"IF (SELECTEDVALUE (DimScenario[scenario_key]) {comparator} "
            '"prior_m_" & FORMAT ([Selected Prior Strength], "0"), '
            "[Prior Sensitivity Improvement])",
        )
    add(
        "Scenario Protocol Status",
        """IF (HASONEVALUE (DimScenario[scenario_key]),
    SWITCH (TRUE (),
        SELECTEDVALUE (DimScenario[is_prespecified_summary]), "Prespecified summary",
        SELECTEDVALUE (DimScenario[classification]) = "preplanned_sensitivity",
            "Preplanned",
        SELECTEDVALUE (DimScenario[classification]) = "supplemental_sensitivity",
            "Supplemental",
        SELECTEDVALUE (DimScenario[classification]) = "post_hoc_exploratory",
            "Post hoc exploratory"))""",
        "",
    )
    add(
        "Cutoff Sensitivity Improvement",
        "CALCULATE ([Scenario Improvement], "
        'KEEPFILTERS (DimScenario[scenario_family] = "historical_cutoff"))',
    )
    return measures
