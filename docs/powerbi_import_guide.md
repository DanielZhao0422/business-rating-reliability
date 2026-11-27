# Power BI implementation and refresh

The local presentation uses a 15-table import model with six active, single-direction relationships. Power Query reads all 15 CSVs through `ExportRoot`. DAX supplies fixed study KPIs, filtered views and explanatory tooltips. The [model contract](powerbi_data_model.md) describes joins and grains. [Report layout](../src/rating_reliability/powerbi_pages.py), [measure code](../src/rating_reliability/powerbi_measures.py) and the [PBIP builder](../src/rating_reliability/powerbi_project.py) generate the five native pages.

| Page | Purpose |
| --- | --- |
| Overview | Main holdout result and historical review-volume comparisons |
| Ratings | Adjustment size, seller evidence and model-conditional credible intervals |
| Rankings | Ties, signed rank movement and feasible Top-K overlap |
| Validation | Bootstrap uncertainty, predictive coverage and sensitivity analysis |
| Operations | Rating–lateness associations, intervals and delivery counts |

The corrected local presentation was refreshed, saved and reopened in Power BI Desktop. Local DAX matched the full corrected export count, **7,535 rows across 15 tables**, and **1,098 paired evaluation sellers**. The owner verified report interaction. The public candidate includes the source and two aggregate-only report captures.

To build a fresh local report, follow the [reproduction guide](reproducibility.md#generate-the-local-power-bi-project). With the generated PBIP open, use **Home → Transform data → Edit parameters** if you need to change the `ExportRoot` directory. Apply changes and select **Home → Refresh**. Save after all queries populate. Use **Fit to page** and compare the Overview and Validation figures. In edit mode, use Ctrl+click for on-canvas navigation and reset controls.
