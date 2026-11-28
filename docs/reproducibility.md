# Reproduce the study and reporting workflows

Run the commands from the root of a source checkout. The local preview and
synthetic tests use only files in the public repository. The research and native
Power BI workflows require a separately obtained Olist archive.

## Preview the case study

Serve the publishing directory with Python:

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

Open the [local case study](http://127.0.0.1:8000/) or
[local browser demonstration](http://127.0.0.1:8000/demo/). Both use static
HTML, CSS, and JavaScript. Source and methodology links open on GitHub.
Stop the local server with Ctrl+C.

## Environment and tests

Create a virtual environment with Python 3.12 or 3.13:

```sh
python -m venv .venv
```

Activate it in PowerShell with `.\.venv\Scripts\Activate.ps1`, or in a POSIX shell
with `source .venv/bin/activate`. Then install the package and run the synthetic
checks:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Synthetic tests run without Olist data or Power BI Desktop. If DuckDB reports
an allocation failure on Windows, retry with a few available logical CPUs.
For example, in a dedicated PowerShell session where the first four CPUs are
available, set `(Get-Process -Id $PID).ProcessorAffinity = 15` before
rerunning pytest.

## Source data

Obtain the [Olist archive](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
independently under its applicable terms and extract the CSVs directly into
`data/raw/olist/`. The source audit inventories orders, items, reviews, sellers,
products, customers, geolocation, payments, and category translations.
Preparation uses orders, items, reviews, sellers, and products. See the
[publication boundary](data_attribution.md) and [analytical integration](olist_integration.md).

## Build and analyze

Run from the source checkout with fresh output paths. The locked protocol uses the review answer timestamp to place scores in historical, validation and subsequent periods.

```sh
python -m rating_reliability.olist_audit --data-dir data/raw/olist --output-dir reports/local/olist_audit
python -m rating_reliability.olist_prepare --data-dir data/raw/olist --database data/processed/olist_analytics.duckdb --output-dir reports/local/olist_preparation
python -m rating_reliability.statistical_analysis --database data/processed/olist_analytics.duckdb --config config/statistical_protocol.toml --output-dir reports/local/statistical_analysis
python -m rating_reliability.phase3c_analysis --database data/processed/olist_analytics.duckdb --config config/statistical_protocol.toml --phase3b-dir reports/local/statistical_analysis --output-dir reports/local/phase3c
```

The principal corrected checkpoints are 95,245 eligible ratings, 40,321
historical ratings, 54,924 subsequent ratings and 1,098 paired sellers. In
`reports/local/statistical_analysis/`, verify strength 20 in
`prior_tuning.json` and raw and adjusted seller-equal MAE of 0.693688 and
0.538918 stars in `model_comparison.json`. The robustness and operational
reports are under `reports/local/phase3c/`.

## Generate the local Power BI project

In a private PowerShell session, set an ephemeral pseudonymization key.

```powershell
$env:POWERBI_SELLER_KEY_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(32))")
```

In a POSIX shell, use `export POWERBI_SELLER_KEY_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"`. Run the following commands in the same shell. The fresh workspace generator uses the default export directory and writes a new report named `desktop`.

```sh
python -m rating_reliability.powerbi_export --database data/processed/olist_analytics.duckdb --config config/statistical_protocol.toml --export-spec config/powerbi_export_spec.toml --phase3b-dir reports/local/statistical_analysis --phase3c-dir reports/local/phase3c --output-dir reports/local/powerbi
python -m rating_reliability.powerbi_project --name desktop
```

Remove the key from your environment afterwards. Regenerate and refresh all 15 tables together whenever the key changes. Exporting with `--overwrite` can replace the entire export directory, including nested Desktop projects. Use a fresh output directory to preserve prior work.

Open `reports/local/powerbi/desktop/RatingReliability.pbip` on Windows in
Power BI Desktop. Verify that `ExportRoot` points to
`reports/local/powerbi/exports`, refresh the model, and run `Validation.dax`
in DAX Query View. Compare the results with the expected values in
`build_metadata.json`. See the [Power BI guide](powerbi_import_guide.md).
