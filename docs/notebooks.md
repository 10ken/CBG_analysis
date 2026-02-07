# Notebook Documentation (notebook/)

This guide summarizes each notebook, its purpose, inputs/outputs, and how to run it with the current file layout.

## Current Workflow
- Run end-to-end: `notebook/99_run_all_pipeline.ipynb` (or `python -m src.run_pipeline`) to regenerate `data/outputs/` and refresh the USDA cache export.
- QA loop: review `data/outputs/assumptions_report.csv` and `data/outputs/manual_review_foods.csv`; fold fixes into `config/defaults_food_items.yaml` or the USDA cache parquet, then rerun 99.
- Guardrails: execute `notebook/05_validation_tests.ipynb` to ensure parsing/defaults/USDA coverage and lags remain healthy after changes.
- Deep dives: use notebooks 01–04 for targeted debugging (ingestion, parsing/units, USDA cache, aggregation/model table) without touching main outputs.

### When to use which notebook (quick picker)
- Need a full refresh and outputs: run 99.
- Checking raw ingestion or datetime sanity: run 01.
- Parsing/units/grams look off: run 02.
- USDA coverage or cache hit rate is low: run 03.
- Meal features or lags feel wrong: run 04.
- Regression/guardrail check after edits: run 05.

## Shared Setup
- Repo root reference: repo_root = Path('..').resolve()
- Primary input Excel: data/source_data/20251218_Trudy_Meals.xlsx
- Outputs from pipeline runs: data/outputs/
- USDA cache parquet: data/parquet/food_nutrition_cache.parquet (unless overridden in notebook cache_data/parquet for USDA/aggregation notebooks).
- API keys: config/api_keys.json (field USDA_food_data).
- Defaults config: config/defaults_food_items.yaml

## Notebook Index

### 01_ingest_and_profile.ipynb
Purpose: Inspect ingestion outputs and summarize datetime/cbg completeness.
Key steps:
1) Load clean events via io_excel.load_clean_events(excel_path).
2) Display repo_root and basic stats on datetime and cbg_post.
Notes: Emits pandas warnings if date/time formats are inconsistent; timestamps are parsed with fallbacks.

### 02_food_parsing_and_units.ipynb
Purpose: Explode meals into item-level rows and apply defaults/gram conversions.
Key steps:
1) Load clean events (source_data Excel).
2) explode_food_items -> apply_default_rules -> compute_grams.
3) Show grams_final distribution and items flagged with assumptions/defaults.
Outputs: In-memory only; no writes.

### 03_usda_cache_and_enrichment.ipynb
Purpose: Attach USDA nutrients and review cache/miss status.
Key steps:
1) Load clean events and explode/apply defaults/grams.
2) Enrich via usda_client.enrich_items_with_usda using cache_path notebook/cache_data/parquet/food_nutrition_cache.parquet.
3) Print cache size, match status counts, and list missing items.
Outputs: Updates notebook-local cache parquet; no CSV writes.

### 04_meal_aggregation_and_model_table.ipynb
Purpose: Build meal-level features and the modeling table.
Key steps:
1) Same prep as USDA notebook, using the same notebook-local cache.
2) compute_item_macros -> build_meal_features -> build_model_table.
3) Preview item macros, meal features, and model table columns (including cbg_prev_same_day lags).
Outputs: In-memory; cache parquet may be updated.

### 05_validation_tests.ipynb
Purpose: Lightweight unit and integration checks for parsing/defaults and pipeline outputs.
Key steps:
1) Unit tests for fractions, parsing, defaults, and grams conversions.
2) Integration checks against data/outputs CSVs (regenerates via run_pipeline if missing).
Assertions ensure grams_final > 0, meal_id uniqueness, cbg_prev_same_day presence, and reasonable assumed_rate.
Outputs consumed/produced:
- Consumes: data/outputs/*.csv from the pipeline; will regenerate them if absent.
- Produces: console/log output only; validation_report.json is already produced by the pipeline run (not by this notebook).

### 99_run_all_pipeline.ipynb
Purpose: Execute the full deterministic pipeline end-to-end and view summaries.
Key steps:
1) Reload core modules, set paths (source_data Excel, api_keys.json, defaults, output_dir).
2) run_pipeline.run_pipeline(...) to regenerate all CSVs/JSONs under data/outputs.
3) Quick health checks on assumptions_report and manual_review_foods.
Outputs written: clean_events.csv, food_items.csv, meal_features.csv, model_table.csv, assumptions_report.csv, manual_review_foods.csv, food_nutrition_cache.csv, validation_report.json (all under data/outputs).
How outputs re-enter workflow:
- clean_events.csv and food_items.csv can be read by downstream analysis or alternative feature engineering without re-running the pipeline.
- meal_features.csv and model_table.csv are the starting point for modeling notebooks/scripts; they already include lags and temporal features.
- assumptions_report.csv and manual_review_foods.csv support manual QA; curated fixes (e.g., better defaults or cache edits) should be reflected back into config/defaults_food_items.yaml or the USDA cache parquet so reruns improve.
- validation_report.json should be reviewed after runs; compare over time to spot regressions in parsing/defaults/usda coverage.

### data_transformation.ipynb
Purpose: Scratchpad for ad-hoc transformations before integrating into the main pipeline. Currently contains a placeholder cell only.

## Running Notes
- Kernels: notebooks are configured for the repo's .venv (Python 3.9). Ensure dependencies (pyarrow, fastparquet, requests, pyyaml) are installed if running elsewhere.
- Logging: run_pipeline emits stage-level logs (cbg_pipeline). In notebooks, logs appear in cell output; in scripts, configure logging.basicConfig as needed.
- Paths: notebooks use repo_root to resolve relative paths; if moving notebooks, update paths consistently.

## How notebooks connect to each other and src
- 99_run_all_pipeline is the canonical orchestrator; it calls src.run_pipeline which executes every stage and writes all outputs to data/outputs. Other notebooks can rely on these outputs instead of recomputing.
- 01_ingest_and_profile is an inspection entry point; it depends on io_excel.load_clean_events and does not write outputs. It can be run first to sanity-check input parsing.
- 02_food_parsing_and_units builds on 01’s ingestion logic (re-calls io_excel) and feeds into 03/04 conceptually by showing item parsing and grams; it does not persist files.
- 03_usda_cache_and_enrichment and 04_meal_aggregation_and_model_table mirror the pipeline steps but use a notebook-local USDA cache at notebook/cache_data/parquet/.... They are useful for focused USDA and aggregation experiments without touching the main cache or outputs.
- 05_validation_tests consumes pipeline outputs in data/outputs; if files are missing it will call src.run_pipeline to regenerate. This notebook is the guardrail to check that parsing/defaults/macros and lags look healthy.
- Manual validation loop: use assumptions_report.csv and manual_review_foods.csv (from 99_run_all_pipeline outputs) to identify items needing better defaults or USDA matches; update config/defaults_food_items.yaml or seed the USDA cache accordingly, then rerun 99 or 05 to verify improvements.
- data_transformation is isolated; use it to prototype before promoting logic into src modules and the orchestrated notebooks.
