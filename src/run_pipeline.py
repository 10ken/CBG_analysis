"""End-to-end pipeline orchestrator for the CBG meal analysis project.

Runs ingestion → parsing → defaults → grams → USDA enrichment → aggregation →
model-table export so we can relate meals to post-meal glucose trends.
"""
from __future__ import annotations

from pathlib import Path
import json
import logging
import os

import yaml

import pandas as pd

from . import aggregate, defaults, io_excel, parse_foods, usda_client, validate
from . import units


logger = logging.getLogger("cbg_pipeline")


def _setup_logger() -> None:
    """Ensure a basic console logger exists for pipeline progress."""
    if logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(asctime)s - %(message)s",
    )


def _stringify_object(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].astype(str)
    return out


def _load_grams_overrides(path: Path) -> tuple[dict, dict]:
    if not path.exists():
        return units.GRAMS_PER_CUP, units.GRAMS_PER_COUNT
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    grams_per_cup = {**units.GRAMS_PER_CUP, **(data.get("grams_per_cup") or {})}
    grams_per_count = {**units.GRAMS_PER_COUNT, **(data.get("grams_per_count") or {})}
    return grams_per_cup, grams_per_count


def run_pipeline(
    excel_path: Path,
    api_keys_path: Path,
    defaults_path: Path,
    grams_overrides_path: Path | None = None,
    output_dir: Path | None = None,
    cache_root: Path | None = None,
    debug: bool = False,
    throttle_enabled: bool = True,
    throttle_max_seconds: float | None = None,
    throttle_batch_size: int | None = None,
    throttle_batch_pause_seconds: float | None = None,
) -> dict:
    """Execute the deterministic meal-to-glucose pipeline and persist outputs."""
    _setup_logger()
    stage = "init"
    try:
        logger.info("Pipeline start")
        base_dir = Path(__file__).resolve().parents[1]
        output_dir = output_dir or (base_dir / "data" / "outputs")
        output_dir.mkdir(parents=True, exist_ok=True)

        cache_root = cache_root or (base_dir / "data" / "cache_data")
        cache_root.mkdir(parents=True, exist_ok=True)
        parquet_dir = cache_root / "parquet"
        parquet_dir.mkdir(parents=True, exist_ok=True)

        cache_path = parquet_dir / "food_nutrition_cache.parquet"
        cache_csv_path = cache_root / "food_nutrition_cache.csv"
        stored_info_path = cache_root / "stored_fdc_id_info.csv"
        stored_info_parquet = parquet_dir / "stored_fdc_id_info.parquet"

        text_replacements_path = base_dir / "config" / "text_replacements.yaml"
        grams_per_cup_map, grams_per_count_map = _load_grams_overrides(
            grams_overrides_path or base_dir / "config" / "grams_overrides.yaml"
        )

        stage = "load_excel"
        logger.info("Loading Excel from %s", excel_path)
        clean_events = io_excel.load_clean_events(excel_path)
        logger.info("Loaded clean events: %d rows", len(clean_events))
        clean_events.to_csv(output_dir / "clean_events.csv", index=False)

        stage = "explode_items"
        logger.info("Exploding meals into item rows")
        items = parse_foods.explode_food_items(clean_events)
        logger.info("Items exploded: %d rows across %d meals", len(items), items["meal_id"].nunique())

        stage = "parsing_audit"
        audit = validate.parsing_diagnostics(items)
        with open(output_dir / "parsing_audit.json", "w", encoding="utf-8") as f:
            json.dump(audit, f, indent=2)

        stage = "apply_defaults"
        logger.info("Applying default rules from %s", defaults_path)
        cfg = defaults.load_defaults_config(defaults_path)
        items = defaults.apply_default_rules(items, cfg, grams_per_count_map=grams_per_count_map)
        logger.info("Defaults applied; assumed_100g_flag rate=%.3f", items.get("assumed_100g_flag", pd.Series(dtype=float)).mean())

        stage = "compute_grams"
        logger.info("Computing grams for items")
        items = aggregate.compute_grams(
            items,
            grams_per_cup_map=grams_per_cup_map,
            grams_per_count_map=grams_per_count_map,
        )

        stage = "usda_enrich"
        logger.info("Enriching with USDA cache/API using %s", cache_path)
        throttle_cfg = {"enabled": throttle_enabled}
        if throttle_max_seconds is not None:
            throttle_cfg["max_seconds"] = throttle_max_seconds
        if throttle_batch_size is not None:
            throttle_cfg["batch_size"] = throttle_batch_size
        if throttle_batch_pause_seconds is not None:
            throttle_cfg["batch_pause_seconds"] = throttle_batch_pause_seconds
        items, cache, stored_info = usda_client.enrich_items_with_usda(
            items,
            api_keys_path,
            cache_path,
            stored_info_path=stored_info_path,
            text_replacements_path=text_replacements_path,
            throttle_config=throttle_cfg,
        )
        logger.info("USDA cache size after enrich: %d rows", len(cache))
        if "fdcId" in cache.columns:
            cache["fdcId"] = cache["fdcId"].astype("string")
        if "fdcId" in stored_info.columns:
            stored_info["fdcId"] = stored_info["fdcId"].astype("string")
        cache.to_csv(cache_csv_path, index=False)
        cache.to_parquet(cache_path, index=False)
        stored_info.to_csv(stored_info_path, index=False)
        stored_info.to_parquet(stored_info_parquet, index=False)

        stage = "item_macros"
        logger.info("Computing item macros")
        items = aggregate.compute_item_macros(items)
        items.to_csv(output_dir / "food_items.csv", index=False)

        stage = "meal_features"
        logger.info("Building meal features")
        meal_features = aggregate.build_meal_features(items, clean_events)
        meal_features.to_csv(output_dir / "meal_features.csv", index=False)

        stage = "model_table"
        logger.info("Building model table")
        model_table = aggregate.build_model_table(meal_features)
        model_table.to_csv(output_dir / "model_table.csv", index=False)

        stage = "reports"
        logger.info("Building assumption and manual review reports")
        assumptions_report = aggregate.build_assumptions_report(items)
        assumptions_report.to_csv(output_dir / "assumptions_report.csv", index=False)

        manual_review = aggregate.build_manual_review(items)
        manual_review.to_csv(output_dir / "manual_review_foods.csv", index=False)

        stage = "validation"
        logger.info("Running validations")
        validation_report = validate.run_all_validations(items, meal_features, model_table, clean_events)
        with open(output_dir / "validation_report.json", "w", encoding="utf-8") as f:
            json.dump(validation_report, f, indent=2, default=str)

        effective_debug = debug or bool(int(os.getenv("CBG_DEBUG", "0")))
        if effective_debug:
            logger.info("Running parsing validation harness (debug mode)")
            parsing_examples = validate.validate_parsing_examples(
                defaults_path=defaults_path,
                grams_overrides_path=grams_overrides_path,
            )
            parsing_examples.to_csv(output_dir / "parsing_examples.csv", index=False)

        summary = {
            "clean_events_rows": len(clean_events),
            "items_rows": len(items),
            "meal_rows": len(meal_features),
            "model_rows": len(model_table),
            "assumptions_rows": len(assumptions_report),
            "manual_review_rows": len(manual_review),
        }
        logger.info("Pipeline complete; summary: %s", summary)
        return summary
    except Exception as exc:  # surface stage in case of failure
        logger.exception("Pipeline failed during stage '%s'", stage)
        raise


if __name__ == "__main__":
    base = Path(__file__).resolve().parents[1]
    excel_path = base / "data" / "source_data" / "20251218_Trudy_Meals.xlsx"
    api_keys_path = base / "config" / "api_keys.json"
    defaults_path = base / "config" / "defaults_food_items.yaml"
    summary = run_pipeline(
        excel_path,
        api_keys_path,
        defaults_path,
        grams_overrides_path=base / "config" / "grams_overrides.yaml",
        output_dir=base / "data" / "outputs",
        cache_root=base / "data" / "cache_data",
    )
    print(summary)
