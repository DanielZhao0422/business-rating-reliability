# Power BI implementation and refresh

The completed native presentation uses 15 imported tables, six active
single-direction relationships, and 102 DAX measures. Power Query reads the
typed CSV exports through `ExportRoot`. DAX supports fixed study KPIs, filtered
views, and tooltips. The [model contract](powerbi_data_model.md) describes the
relationships and table grains. The [report layout](../src/rating_reliability/powerbi_pages.py),
[measure definitions](../src/rating_reliability/powerbi_measures.py), and
[PBIP builder](../src/rating_reliability/powerbi_project.py) generate the five pages.

| Page | Purpose |
| --- | --- |
| Overview | Main holdout result and historical review-volume comparisons |
| Ratings | Adjustment size, seller evidence and model-conditional credible intervals |
| Rankings | Ties, signed rank movement and feasible Top-K overlap |
| Validation | Bootstrap uncertainty, predictive coverage and sensitivity analysis |
| Operations | Rating–lateness associations, intervals and delivery counts |

The corrected presentation was refreshed, saved, and reopened in Power BI
Desktop. Local DAX matched the corrected export count of **7,535 rows across
15 tables** and **1,098 paired evaluation sellers**. The public
[browser demonstration](https://danielzhao0422.github.io/business-rating-reliability/demo/)
shows two aggregate-only Desktop captures, the relationship diagram, and
selected DAX expressions. The complete native report and populated model stay local.

To build a fresh local report, follow the [reproduction guide](reproducibility.md#generate-the-local-power-bi-project). With the generated PBIP open, use **Home → Transform data → Edit parameters** if you need to change the `ExportRoot` directory. Apply changes and select **Home → Refresh**. Save after all queries populate. Use **Fit to page** and compare the Overview and Validation figures. In edit mode, use Ctrl+click for on-canvas navigation and reset controls.
