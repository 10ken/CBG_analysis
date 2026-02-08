"""Validation utilities for pipeline outputs in the CBG meal analysis.

Provides lightweight checks to ensure meal items, meal features, and model
tables stay consistent before interpreting glucose outcomes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import yaml

from .aggregate import compute_grams
from .defaults import apply_default_rules, load_defaults_config
from .parse_foods import _split_items
from .units import load_text_replacements, normalize_food_text_for_usda, parse_quantity_unit


def _required_columns(df: pd.DataFrame, cols: List[str], name: str):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name} missing required columns: {missing}")


def validate_items(items: pd.DataFrame) -> Dict:
    _required_columns(items, ["grams_final", "meal_id", "event_id", "sheet_source"], "items")
    if (items["grams_final"] <= 0).any():
        raise ValueError("grams_final must be positive for all items")
    return {
        "assumed_rate": float((items.get("assumed_100g_flag", False)).mean()),
        "usda_missing_rate": float((items.get("usda_match_status") == "missing").mean()),
    }


def validate_meals(meal_features: pd.DataFrame) -> Dict:
    _required_columns(meal_features, ["meal_id", "datetime", "blood_glucose_2hrs_post", "sheet_source"], "meal_features")
    if meal_features["meal_id"].duplicated().any():
        raise ValueError("Duplicate meal_id detected")
    if meal_features["datetime"].isna().any():
        raise ValueError("Unparseable datetime detected in meal_features")
    bg_numeric = pd.to_numeric(meal_features["blood_glucose_2hrs_post"], errors="coerce")
    non_convertible = bg_numeric.isna() & meal_features["blood_glucose_2hrs_post"].notna()
    if non_convertible.any():
        raise ValueError("blood_glucose_2hrs_post must be numeric or NaN")
    return {
        "meals": len(meal_features),
        "avg_items_per_meal": float(meal_features.get("items_count", 0).mean()),
    }


def validate_model(model_table: pd.DataFrame) -> Dict:
    _required_columns(model_table, ["cbg_prev_same_day", "meal_id", "sheet_source"], "model_table")
    return {
        "model_rows": len(model_table),
    }


def validate_meal_type(meal_features: pd.DataFrame) -> Dict:
    if "meal_type" not in meal_features.columns:
        raise ValueError("meal_features missing required column: meal_type")
    allowed = {"breakfast", "lunch", "dinner", "snack", "fasting", None}
    present = set(meal_features["meal_type"].dropna().str.lower().unique())
    unexpected = present - allowed
    if unexpected:
        raise ValueError(f"Unexpected meal_type values: {sorted(unexpected)}")
    return {"meal_type_values": sorted(present)}


def normalization_examples(text_replacements_path: Path | None = None) -> pd.DataFrame:
    samples = [
        "BBQ pork (char siu)",
        "grn veggy stir-fry",
        "bar-b-q chicken",
        "wood fungus salad",
    ]
    replacements = load_text_replacements(text_replacements_path)
    rows = []
    for raw in samples:
        rows.append(
            {
                "raw": raw,
                "normalized": normalize_food_text_for_usda(raw, replacements=replacements),
            }
        )
    return pd.DataFrame(rows)


def run_all_validations(
    items: pd.DataFrame,
    meal_features: pd.DataFrame,
    model_table: pd.DataFrame,
    clean_events: pd.DataFrame | None = None,
) -> Dict:
    results = {
        "items": validate_items(items),
        "meals": validate_meals(meal_features),
        "model": validate_model(model_table),
    }
    try:
        results["meal_type"] = validate_meal_type(meal_features)
    except Exception as exc:
        raise
    if clean_events is not None:
        # ensure clean_events meal_type consistent
        clean_allowed = {"breakfast", "lunch", "dinner", "snack", "fasting", None}
        if "meal_type" in clean_events.columns:
            unexpected = set(clean_events["meal_type"].dropna().str.lower().unique()) - clean_allowed
            if unexpected:
                raise ValueError(f"Unexpected meal_type values in clean_events: {sorted(unexpected)}")
    return results


def demo_parsing_harness(cases: List[str] | None = None, defaults_path: Path | None = None) -> pd.DataFrame:
    """Quick, deterministic harness to inspect quantity/unit parsing.

    Returns a DataFrame (also prints) with parsed fields and grams_final so
    fraction parsing and assumption handling stay auditable.
    """

    if cases is None:
        cases = [
            "blueberries/1/2 kiwi",
            "0.5 cup blanched choy sum",
            "1/4 cup mixed nuts",
            "3/4 cup quinoa",
            "1 tsp flax seed",
            "2 eggs",
            "1.5 oz of beef",
        ]

    defaults_path = (
        defaults_path
        or Path(__file__).resolve().parents[1]
        / "config"
        / "defaults_food_items.yaml"
    )
    cfg = load_defaults_config(defaults_path)

    rows = []
    for raw in cases:
        for item in _split_items(raw):
            parsed = parse_quantity_unit(item)
            rows.append(
                {
                    "source_raw": raw,
                    "food_text_raw": item,
                    "qty_raw": parsed.qty_raw,
                    "unit_raw": parsed.unit_raw,
                    "qty_numeric": parsed.qty_numeric,
                    "unit_std": parsed.unit_std,
                    "food_name_std": parsed.food_name_std,
                }
            )

    df = pd.DataFrame(rows)
    df = apply_default_rules(df, cfg)
    df = compute_grams(df)
    cols = [
        "source_raw",
        "food_text_raw",
        "qty_raw",
        "qty_numeric",
        "unit_std",
        "food_name_std",
        "grams_final",
        "assumption_reason",
    ]
    print(df[cols])
    return df[cols]


def parsing_diagnostics(items: pd.DataFrame, top_n: int = 50) -> Dict:
    """Summarize parsing gaps for audit reporting."""
    missing_qty_leading_number = items[
        items.get("qty_numeric").isna()
        & items.get("food_text_raw", "").astype(str).str.match(r"^\s*\d")
    ]

    # residual unit/number tokens left in standardized food names
    pattern = r"(\b\d+\b|\b(?:cup|cups|tsp|tbsp|oz|g|gram|slice|slices|count|piece)\b)"
    residual_mask = items.get("food_name_std", "").astype(str).str.contains(pattern, regex=True, na=False)
    residual_counts = (
        items.loc[residual_mask, "food_text_raw"]
        .fillna("")
        .value_counts()
        .head(top_n)
    )
    return {
        "missing_qty_with_leading_number": int(len(missing_qty_leading_number)),
        "residual_unit_or_number_examples": residual_counts.to_dict(),
    }


def _load_grams_overrides(grams_overrides_path: Path | None) -> Tuple[Dict, Dict]:
    from . import units

    if grams_overrides_path is None or not grams_overrides_path.exists():
        return units.GRAMS_PER_CUP, units.GRAMS_PER_COUNT
    data = yaml.safe_load(grams_overrides_path.read_text(encoding="utf-8")) or {}
    gpc = {**units.GRAMS_PER_CUP, **(data.get("grams_per_cup") or {})}
    gcount = {**units.GRAMS_PER_COUNT, **(data.get("grams_per_count") or {})}
    return gpc, gcount


def validate_parsing_examples(
    defaults_path: Path | None = None,
    grams_overrides_path: Path | None = None,
) -> pd.DataFrame:
    """Deterministic harness to ensure parsing/grams stay stable."""

    cases = [
        "2 eggs",
        "1 oz cheese",
        "1 tsp flax seed",
        "2 slices of pizza",
        "2 cups veggies",
        "1/4 cup mixed nuts",
        "1 4 cup mixed nuts",
        "1/2 slice of bread",
        "1 2 slice of bread",
        "brocoli",
        "broccolli",
        "brocolli",
    ]

    defaults_path = (
        defaults_path
        or Path(__file__).resolve().parents[1]
        / "config"
        / "defaults_food_items.yaml"
    )
    cfg = load_defaults_config(defaults_path)
    grams_per_cup_map, grams_per_count_map = _load_grams_overrides(grams_overrides_path)

    rows = []
    for raw in cases:
        parsed = parse_quantity_unit(raw)
        rows.append(
            {
                "food_text_raw": raw,
                "qty_raw": parsed.qty_raw,
                "qty_numeric": parsed.qty_numeric,
                "unit_std": parsed.unit_std,
                "food_name_std": parsed.food_name_std,
            }
        )

    df = pd.DataFrame(rows)
    df = apply_default_rules(df, cfg, grams_per_count_map=grams_per_count_map)
    df = compute_grams(
        df,
        grams_per_cup_map=grams_per_cup_map,
        grams_per_count_map=grams_per_count_map,
    )
    df["used_100g_fallback"] = df.get("assumed_100g_flag", False)
    return df[
        [
            "food_text_raw",
            "qty_raw",
            "qty_numeric",
            "unit_std",
            "food_name_std",
            "grams_final",
            "used_100g_fallback",
        ]
    ]
