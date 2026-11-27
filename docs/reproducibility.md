# Reproduce the analysis

## Preview the case study

From the candidate root, run:

```sh
python -m http.server 8000 --bind 127.0.0.1
```

Open [http://127.0.0.1:8000/docs/](http://127.0.0.1:8000/docs/). Keep the server running while reviewing the case study and stop it with Ctrl+C. The preview uses standard HTML, CSS and JavaScript. The case study’s technical links point to the intended GitHub repository. Before publication, read the corresponding source and documentation in the local candidate.

## Environment and tests

Use Python 3.12 or 3.13 and create a virtual environment.

```sh
python -m venv .venv
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Activate the environment before installing. Use `.\.venv\Scripts\Activate.ps1` in PowerShell or `source .venv/bin/activate` in a POSIX shell. Synthetic tests run without Olist data or Desktop. If DuckDB reports an allocation failure in a Windows test run, limiting the test process to a few supported logical CPUs reduced resource use during local verification. For example, set `(Get-Process -Id $PID).ProcessorAffinity = 15` in a dedicated PowerShell session where the first four CPUs are available, then rerun pytest.

## Source data

Obtain the [Olist archive](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) independently under its applicable terms and extract it to `data/raw/olist/`. The source audit inventories the nine files. Preparation requires orders, items, reviews, sellers, and products. See [data attribution](data_attribution.md) and [analytical integration](olist_integration.md).

## Build and analyze

Run from the source checkout with fresh output paths. The locked protocol uses the review answer timestamp to place scores in historical, validation and subsequent periods.

```sh
python -m rating_reliability.olist_audit --data-dir data/raw/olist --output-dir reports/local/olist_audit
python -m rating_reliability.olist_prepare --data-dir data/raw/olist --database data/processed/olist_analytics.duckdb --output-dir reports/local/olist_preparation
python -m rating_reliability.statistical_analysis --database data/processed/olist_analytics.duckdb --config config/statistical_protocol.toml --output-dir reports/local/statistical_analysis
python -m rating_reliability.phase3c_analysis --database data/processed/olist_analytics.duckdb --config config/statistical_protocol.toml --phase3b-dir reports/local/statistical_analysis --output-dir reports/local/phase3c
```

The principal corrected checkpoints are 95,245 eligible ratings, 40,321 historical ratings, 54,924 subsequent ratings and 1,098 paired sellers. Look for strength 20 in `prior_tuning.json` and raw/adjusted seller-equal MAE of 0.693688 / 0.538918 stars in `model_comparison.json`.

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

Open the generated `RatingReliability.pbip` on Windows in Power BI Desktop. Verify that `ExportRoot` points to the local `reports/local/powerbi/exports` directory, refresh the model, and compare `Validation.dax` with `build_metadata.json`. See the [Power BI guide](powerbi_import_guide.md) for details.
