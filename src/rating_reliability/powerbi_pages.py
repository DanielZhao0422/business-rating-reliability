"""Five native report pages and their explicit visual interaction scopes."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Visual:
    key: str
    kind: str
    title: str
    roles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    targets: tuple[str, ...] = ()
    sort: str = ""
    axes: tuple[tuple[str, float, float], ...] = ()
    colors: tuple[str, ...] = ()
    labels: bool = False
    log_x: bool = False
    below: Visual | None = None


@dataclass(frozen=True)
class Page:
    name: str
    title: str
    subtitle: str
    rows: tuple[tuple[int, tuple[Visual, ...]], ...]
    footnote: str


NAVY = "#172E42"
STEEL = "#4472A0"
TEAL = "#10867F"
AMBER = "#C47C24"
CANVAS = "#F3F6FA"
MUTED = "#455A6D"
VOLUME = "DimReviewVolume.display_label"
METHOD = "DimMethod.method_label"
SELLER = "DimSeller.seller_label"
SCENARIO = "DimScenario.scenario_label"
TOP_K = "FactRankingSummary.top_k"


def card(measure: str, label: str) -> Visual:
    return Visual(measure, "card", label, {"Values": (measure,)})


def note(key: str, heading: str, *paragraphs: str) -> Visual:
    return Visual(key, "textbox", "\n".join((heading, *paragraphs)))


def slicer(column: str, title: str, *targets: str) -> Visual:
    return Visual(column, "slicer", title, {"Values": (column,)}, targets)


def table(key: str, title: str, *columns: str, sort: str = "") -> Visual:
    return Visual(key, "tableEx", title, {"Values": columns}, sort=sort)


def chart(
    key: str,
    kind: str,
    title: str,
    category: str,
    *values: str,
    tooltips: tuple[str, ...] = (),
    bounds: tuple[float, float] | None = None,
    colors: tuple[str, ...] = (),
    labels: bool = False,
) -> Visual:
    roles = {"Y": values, "Category": (category,)}
    if tooltips:
        roles["Tooltips"] = tooltips
    return Visual(
        key,
        kind,
        title,
        roles,
        sort=category,
        axes=(("valueAxis", *bounds),) if bounds else (),
        colors=colors,
        labels=labels,
    )


def scatter(
    key: str,
    title: str,
    x: str,
    y: str,
    *,
    x_range: tuple[float, float] = (1, 5),
    y_range: tuple[float, float],
    tooltips: tuple[str, ...],
    targets: tuple[str, ...] = (),
    log_x: bool = False,
) -> Visual:
    return Visual(
        key,
        "scatterChart",
        title,
        {"Category": (SELLER,), "X": (x,), "Y": (y,), "Tooltips": tooltips},
        targets=targets,
        axes=(("categoryAxis", *x_range), ("valueAxis", *y_range)),
        log_x=log_x,
    )


def report_pages() -> tuple[Page, ...]:
    return (
        Page(
            "Overview",
            "Can we trust a seller’s average rating?",
            "Marketplace sellers  •  Historical estimates tested against later ratings",
            (
                (
                    105,
                    (
                        card(
                            "Relative MAE Improvement",
                            "Lower seller-equal prediction MAE vs. raw",
                        ),
                        card("Raw MAE", "Raw MAE (stars)"),
                        card("Adjusted MAE", "Adjusted MAE (stars)"),
                        card("Evaluation Sellers", "Paired evaluation sellers"),
                    ),
                ),
                (
                    306,
                    (
                        chart(
                            "volume",
                            "clusteredColumnChart",
                            "Error fell most for sellers with 1–4 historical reviews",
                            VOLUME,
                            "Volume Raw MAE",
                            "Volume Adjusted MAE",
                            tooltips=(
                                "Volume Evaluation Sellers",
                                "Volume Historical Ratings",
                                "Volume Evaluation Ratings",
                                "Volume MAE Improvement",
                            ),
                            bounds=(0, 1.2),
                            colors=(STEEL, TEAL),
                            labels=True,
                        ),
                        note(
                            "overview_story",
                            "A 5-star average needs context",
                            "Illustrative example. Two sellers both average 5 stars. "
                            "One has two reviews and the other has hundreds.",
                            "",
                            "Show review count and uncertainty alongside each rating "
                            "so readers can judge the evidence behind it.",
                            "",
                            "MAE is mean absolute error in stars. Each paired seller "
                            "contributes equally.",
                        ),
                    ),
                ),
                (
                    76,
                    (
                        card("Eligible Ratings", "Eligible ratings"),
                        card("Rated Sellers", "Rated sellers (full study)"),
                        card("Historical Sellers", "Sellers with historical ratings"),
                        card("Historical Observations", "Historical ratings"),
                        card("Subsequent Observations", "Subsequent ratings"),
                    ),
                ),
            ),
            "Ratings were split by answer time at January 1, 2018. "
            "The evaluation uses sellers with ratings on both sides of that cutoff.",
        ),
        Page(
            "Ratings",
            "How much evidence supports a seller’s rating?",
            "Adjustment pulls historical ratings toward the pooled reference. "
            "Sellers with fewer reviews tend to move further.",
            (
                (
                    76,
                    (
                        slicer(
                            VOLUME,
                            "Historical Review Volume",
                            SELLER,
                            "paired",
                            "adjustment",
                            "Selected Historical Sellers",
                            "seller_evidence",
                        ),
                        slicer(SELLER, "Seller", "paired", "seller_evidence"),
                        card(
                            "Selected Historical Sellers", "Historical sellers in view"
                        ),
                        card("Historical Reference Mean", "Pooled reference (stars)"),
                        card(
                            "Selected Prior Strength",
                            "Prior strength (equivalent reviews)",
                        ),
                    ),
                ),
                (
                    294,
                    (
                        scatter(
                            "paired",
                            "Individual adjustment by historical review count",
                            "Selected Historical Review Count",
                            "Mean Absolute Rating Adjustment",
                            x_range=(1, 1100),
                            y_range=(0, 3.2),
                            log_x=True,
                            tooltips=(
                                "Raw Historical Rating",
                                "Adjusted Historical Rating",
                                "Expected Rating Interval Lower",
                                "Expected Rating Interval Upper",
                            ),
                            targets=("seller_evidence",),
                        ),
                        chart(
                            "adjustment",
                            "clusteredColumnChart",
                            "Mean absolute adjustment by review band",
                            VOLUME,
                            "Mean Absolute Rating Adjustment",
                            tooltips=("Selected Historical Sellers",),
                            bounds=(0, 1),
                            colors=(TEAL,),
                            labels=True,
                        ),
                    ),
                ),
                (
                    112,
                    (
                        card(
                            "Seller Evidence Display",
                            "Seller evidence and expected-rating credible interval",
                        ),
                    ),
                ),
            ),
            "One dot per historical seller. The review-count axis is logarithmic. "
            "The seller’s expected-rating interval is conditional on the model.",
        ),
        Page(
            "Rankings",
            "Can we trust an exact seller rank?",
            "Many sellers share the same raw rating, and adjustment can move them "
            "substantially within the paired cohort.",
            (
                (
                    94,
                    (
                        card("Raw Sellers in Ties", "Sellers tied on raw rating"),
                        card(
                            "Mean Absolute Rank Movement",
                            "Mean absolute movement (positions)",
                        ),
                        card("Largest Raw Rating Tie", "Sellers in largest raw tie"),
                        card(
                            "Ranking Sellers", "Fixed ranking universe (paired sellers)"
                        ),
                    ),
                ),
                (
                    76,
                    (
                        slicer(TOP_K, "Shortlist Size", "overlap"),
                        card("Spearman", "Spearman rank agreement"),
                        card("Kendall Tau-b", "Kendall tau-b rank agreement"),
                    ),
                ),
                (
                    318,
                    (
                        scatter(
                            "ranks",
                            "Raw rank and the shift after adjustment",
                            "Raw Average Rank",
                            "Signed Rank Movement",
                            tooltips=(
                                "Adjusted Average Rank",
                                "Selected Historical Review Count",
                                "Posterior Top 10 Probability",
                                "Posterior Top 25 Probability",
                                "Posterior Top 50 Probability",
                            ),
                            x_range=(0, 1100),
                            y_range=(-500, 500),
                        ),
                        Visual(
                            "overlap",
                            "tableEx",
                            "Feasible raw vs. adjusted shortlist overlap (sellers)",
                            {
                                "Values": (
                                    TOP_K,
                                    "Top-K Feasible Overlap",
                                    "Top-K Fractional Overlap",
                                )
                            },
                            sort=TOP_K,
                            below=note(
                                "rank_story",
                                "Cutoff ties make the shortlist uncertain",
                                "The range covers possible shared-seller counts. "
                                "Expected overlap assumes uniform ordering "
                                "within cutoff ties.",
                                "",
                                "Hover over a seller to see model-conditional "
                                "Top-K membership probabilities.",
                            ),
                        ),
                    ),
                ),
            ),
            "Rank 1 is highest. Ties share average ranks. Zero means equal ranks. "
            "Negative shifts indicate a higher adjusted position. "
            "Rank changes describe stability within this cohort.",
        ),
        Page(
            "Validation",
            "How credible and robust are the findings?",
            "The error reduction is positive in the paired bootstrap interval. "
            "Predictive coverage falls below its target overall.",
            (
                (
                    70,
                    (
                        card("MAE Improvement", "Absolute MAE reduction (stars)"),
                        card(
                            "Paired Bootstrap Interval",
                            "95% paired bootstrap interval (stars)",
                        ),
                        card(
                            "Observed Predictive Coverage",
                            "Observed predictive coverage",
                        ),
                        card(
                            "Nominal Predictive Coverage", "Nominal predictive coverage"
                        ),
                    ),
                ),
                (
                    202,
                    (
                        chart(
                            "prior",
                            "clusteredColumnChart",
                            "Final-period MAE reduction by prior strength (stars)",
                            SCENARIO,
                            "Other Prior Strengths",
                            "Selected Prior Strength Improvement",
                            bounds=(0, 0.2),
                            colors=(STEEL, TEAL),
                        ),
                        chart(
                            "calibration",
                            "lineChart",
                            "Coverage is lowest in the 50+ historical review band",
                            VOLUME,
                            "Volume Nominal Coverage",
                            "Volume Predictive Coverage",
                            tooltips=(
                                "Volume Mean Interval Width",
                                "Volume Calibration Sellers",
                            ),
                            bounds=(0.8, 1),
                            colors=(AMBER, TEAL),
                            labels=True,
                        ),
                    ),
                ),
                (
                    76,
                    (
                        slicer(
                            "DimScenario.scenario_family",
                            "Sensitivity Family",
                            SCENARIO,
                            "scenario_result",
                        ),
                        slicer(SCENARIO, "Sensitivity Scenario", "scenario_result"),
                        note(
                            "validation_story",
                            "Prior strength selected before final evaluation",
                            "Two expanding 2017 folds selected m = 20. "
                            "The bars use final-period data.",
                        ),
                    ),
                ),
                (
                    132,
                    (
                        table(
                            "scenario_result",
                            "Sensitivity comparisons · use the filters to narrow",
                            SCENARIO,
                            "Scenario Protocol Status",
                            "Scenario Raw MAE",
                            "Scenario Adjusted MAE",
                            "Scenario Improvement",
                            "Scenario Sellers",
                            sort=SCENARIO,
                        ),
                    ),
                ),
            ),
            "The historical cutoff uses review answers before January 1, 2018. "
            "Predictive coverage conditions on each seller’s subsequent rating count.",
        ),
        Page(
            "Operations",
            "Can seller ratings substitute for fulfillment measures?",
            "Raw and adjusted ratings had weak associations with later lateness.",
            (
                (
                    136,
                    (
                        table(
                            "association",
                            "Seller-level Spearman correlation with later lateness",
                            METHOD,
                            "Lateness Spearman",
                            "Lateness CI Lower",
                            "Lateness CI Upper",
                            "Lateness Association Sellers",
                            sort=METHOD,
                        ),
                        note(
                            "operations_story",
                            "Monitor fulfillment directly",
                            "The adjusted estimate’s 95% interval crosses zero. "
                            "These observational associations describe how ratings "
                            "and lateness varied together.",
                        ),
                    ),
                ),
                (
                    76,
                    (
                        slicer(VOLUME, "Historical Review Volume", "lateness"),
                        slicer(METHOD, "Rating Method", "lateness"),
                        note(
                            "operations_default",
                            "Seller scatter",
                            "The scatter uses adjusted ratings by default.",
                        ),
                    ),
                ),
                (
                    186,
                    (
                        scatter(
                            "lateness",
                            "Historical rating and later seller late-delivery rate",
                            "Operational Historical Rating",
                            "Selected Late Delivery Rate",
                            y_range=(0, 1),
                            tooltips=(
                                "Selected Forward Orders",
                                "Selected Evaluable Deliveries",
                                "Selected Late Deliveries",
                                "Selected Operational Coverage",
                            ),
                        ),
                    ),
                ),
                (
                    76,
                    (
                        card("Forward Orders", "Forward orders"),
                        card("Evaluable Deliveries", "Evaluable deliveries"),
                        card("Late Deliveries", "Late deliveries"),
                        card("Late Delivery Rate", "Late-delivery rate"),
                    ),
                ),
            ),
            "Forward orders cover historically rated sellers. "
            "The late rate uses evaluable deliveries. Spearman ranges from −1 to +1.",
        ),
    )
