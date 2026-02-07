# Source Code Documentation (src/)

This document describes the core pipeline modules under src/ that transform raw Excel meal logs into analysis-ready CSVs and a USDA nutrient cache.

## Current Workflow
- Orchestrate: call `run_pipeline.run_pipeline()` (via `python -m src.run_pipeline` or notebook/99) to rebuild `data/outputs/` and emit a fresh USDA cache CSV alongside the canonical parquet.
- Inspect QA: review `assumptions_report.csv` and `manual_review_foods.csv` to spot default-driven grams or USDA misses; adjust `config/defaults_food_items.yaml` or seed `data/parquet/food_nutrition_cache.parquet` accordingly.
- Re-run + validate: rerun the pipeline and confirm `validation_report.json` plus `notebook/05_validation_tests.ipynb` stay green.
- Iterate modules: use src modules directly when prototyping (io_excel -> parse_foods -> defaults -> aggregate -> usda_client -> validate) before wiring changes back into run_pipeline.

## Pipeline Flow
1) io_excel.load_clean_events: normalize Excel sheets into a single events table with meal_id, event_type, datetime, cbg_post, and raw text columns.
2) parse_foods.explode_food_items: explode each meal into item-level rows, parsing quantities/units and normalizing food names.
3) defaults.apply_default_rules: apply YAML-configured defaults when qty/unit or grams overrides are missing; records assumption_reason and default_applied_flag.
4) aggregate.compute_grams: convert qty/unit to grams_final using unit densities and fallbacks; sets assumed_100g_flag when assumptions are applied.
5) usda_client.enrich_items_with_usda: attach per-100g nutrients from cache or USDA API; updates and persists the cache parquet.
6) aggregate.compute_item_macros: multiply grams_final by per-100g nutrients to get item macros.
7) aggregate.build_meal_features: aggregate item macros to meals and add time-based features (hour_sin/hour_cos, time_since_prev_meal_same_day_min).
8) aggregate.build_model_table: add same-day lag features for modeling.
9) aggregate.build_assumptions_report and aggregate.build_manual_review: audit outputs for assumptions and missing USDA matches.
10) validate.run_all_validations: sanity checks on items, meal_features, and model_table.
11) run_pipeline.run_pipeline: orchestrates all stages and writes CSV/JSON outputs.

### Glossary (human-readable)
- assumption_reason: short label describing why we filled qty/unit/grams (e.g., missing_qty_or_unit, unrecognized_unit, missing_grams_per_cup).
- assumed_100g_flag: true when we fell back to 100g because no better unit/grams data existed.
- usda_match_status: cache_hit, api_hit, or missing_usda_match; tells you whether nutrients came from cache, live API, or could not be found.

### One example item, end-to-end
Raw text: "1 cup chopped broccoli"
1) parse_foods -> qty_numeric=1, unit_std=cup, food_name_std=broccoli
2) defaults -> no default applied (default_applied_flag=False)
3) aggregate.compute_grams -> grams_final from grams_per_cup map (e.g., 91g) and assumed_100g_flag=False
4) usda_client -> usda_match_status=cache_hit (pulled per-100g nutrients)
5) aggregate.compute_item_macros -> carbs/protein/fat/energy scaled to 91g
6) aggregate.build_meal_features -> summed into meal macros and time features
7) aggregate.build_model_table -> cbg_prev_same_day added for modeling

## Key Modules
### run_pipeline.py
- run_pipeline(excel_path, api_keys_path, defaults_path, output_dir): orchestrates the full pipeline, persists outputs, and returns a summary dict. Logs stage-by-stage progress via logger cbg_pipeline.

### io_excel.py
- load_clean_events(excel_path): loads all sheets, detects schema (Dec25 Jan26 vs Feb2026), normalizes columns, builds stable event_id, and parses datetime with fallbacks. Outputs meal_id and flags datetime_imputed_flag.

### parse_foods.py
- explode_food_items(clean_events): splits category text (protein/carb/veggies/fat/other/items_text) into rows, parsing qty/unit via units.parse_quantity_unit. Preserves fasting records when no items exist.

### defaults.py
- load_defaults_config(path): read YAML defaults.
- apply_default_rules(items, cfg): apply rule-based or global defaults to fill qty_numeric/unit_std/grams_override; sets default_applied_flag and assumption_reason.

### units.py
- parse_quantity_unit(food_text_raw): extract qty/unit tokens, normalize unit, parse fractions/mixed numbers, and normalize food name.
- convert_to_grams(qty, unit_std, food_name_std, grams_per_cup_map): convert to grams; returns (grams, assumption_reason) with reasons such as missing_qty_or_unit, missing_grams_per_cup, unrecognized_unit.
- GRAMS_PER_CUP mapping and MASS/VOLUME unit constants.

### aggregate.py
- compute_grams(items): derive grams_final with fallbacks to 100g and propagate assumption_reason/assumed_100g_flag.
- compute_item_macros(items_with_grams): calories, carbs_g, protein_g, fat_g from per-100g values.
- build_meal_features(items, clean_events): meal-level aggregation plus circadian features and time since previous meal (same day).
- build_model_table(meal_features): adds same-day lag feature via lag_features.add_same_day_lag.
- build_assumptions_report(items): audit rows where defaults/assumptions applied.
- build_manual_review(items): rows missing USDA matches or using assumptions.

### usda_client.py
- enrich_items_with_usda(items, api_keys_path, cache_path): merges existing cache, calls USDA API for missing foods, saves updated cache parquet, sets usda_match_status, and logs cache/API activity. Relies on api_keys.json["USDA_food_data"].

### validate.py
- validate_items/meals/model: required-column checks and basic sanity assertions.
- run_all_validations: returns aggregated validation metrics.

### lag_features.py
- add_same_day_lag(df): computes cbg_prev_same_day and related lag columns for modeling (imported by aggregate.build_model_table).

## Paths and Outputs
- Input Excel: data/source_data/20251218_Trudy_Meals.xlsx
- Outputs (CSV/JSON): data/outputs/
- USDA cache parquet: data/parquet/food_nutrition_cache.parquet (or notebook-specific cache if overridden in notebooks).

## Outputs and how they re-enter the pipeline
- clean_events.csv: normalized event-level table; can be re-read for profiling or for downstream experiments without reloading Excel.
- food_items.csv: item-level table with qty/unit parsing, defaults, grams_final, USDA nutrients, and macros. Serves as the canonical feature base for any alternative aggregations or model feature engineering.
- food_nutrition_cache.csv (and parquet in data/parquet): nutrient lookup table; reused on subsequent runs to avoid repeated USDA API calls and to enrich new items.
- meal_features.csv: meal-level aggregates plus temporal features (hour_sin/hour_cos, time_since_prev_meal_same_day_min); input to model_table creation and any analysis that works at the meal grain.
- model_table.csv: modeling-ready table with lags (cbg_prev_same_day) and meal features; primary input to downstream modeling/analysis notebooks or scripts.
- assumptions_report.csv: audit of rows where defaults or 100g assumptions were applied; feeds manual QA and informs tightening defaults.
- manual_review_foods.csv: items missing USDA matches or using assumptions; used for manual nutrient curation and can be folded back into cache updates or defaults.
- validation_report.json: validation metrics (assumed_rate, usda_missing_rate, meal_id uniqueness, etc.); should be checked after runs and can be compared across commits to spot regressions.

## Logging
- Logger name: cbg_pipeline. run_pipeline emits stage markers; usda_client logs cache loads/saves and API results. Configure logging.basicConfig in callers if running outside notebooks.

## How the src files connect
- run_pipeline orchestrates and calls everything else in order: io_excel -> parse_foods -> defaults -> aggregate.compute_grams -> usda_client -> aggregate.compute_item_macros -> aggregate.build_meal_features -> aggregate.build_model_table -> aggregate audit reports -> validate.
- io_excel is the only Excel reader; all downstream modules assume its normalized columns (meal_id, event_id, datetime, cbg_post, *_text).
- parse_foods depends on units to parse qty/unit and normalize names; its outputs (qty_numeric, unit_std, food_name_std, food_category) are required by defaults and aggregate.
- defaults consumes YAML rules and writes assumption_reason, default_applied_flag, grams_override that aggregate.compute_grams uses when computing grams_final and assumed_100g_flag.
- aggregate functions expect nutrient columns from usda_client (calories_kcal_100g, carbs_g_100g, protein_g_100g, fat_g_100g) before computing item macros and meal features.
- usda_client is the only USDA/API touchpoint; it reads api_keys.json, merges the cache parquet, and updates usda_match_status so aggregate.build_manual_review can flag misses.
- validate runs last, consuming items, meal_features, and model_table to ensure required columns and basic sanity.
- lag_features is imported by aggregate.build_model_table to add cbg_prev_same_day; it expects meal_features with datetime and meal_id.
