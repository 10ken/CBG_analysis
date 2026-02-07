"""Aggregation helpers for CBG meal logs.

Transforms parsed meal items into grams, macros, and meal-level features so we
can relate logged meals to post-meal glucose responses and build modeling
tables for downstream analysis.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from . import units


ASSUMPTION_DEFAULT_REASON = "default_missing_qty_unit_100g"


def compute_grams(
    items: pd.DataFrame,
    grams_per_cup_map: Dict[str, float] | None = None,
    grams_per_count_map: Dict[str, float] | None = None,
    default_density_g_per_ml: float = units.DEFAULT_DENSITY_G_PER_ML,
    **_: Dict,
) -> pd.DataFrame:
    """Compute grams_final for each item using unit conversions and fallbacks."""
    df = items.copy()
    if "assumed_100g_flag" not in df.columns:
        df["assumed_100g_flag"] = False
    if "assumption_reason" not in df.columns:
        df["assumption_reason"] = None

    grams_per_cup_map = grams_per_cup_map or units.GRAMS_PER_CUP
    grams_per_count_map = grams_per_count_map or units.GRAMS_PER_COUNT

    grams_list = []
    reason_list = []
    assumed_flags = []

    for _, row in df.iterrows():
        existing_reason = row.get("assumption_reason")
        if pd.notnull(row.get("grams_override")):
            grams_list.append(row["grams_override"])
            reason_list.append(existing_reason)
            assumed_flags.append(False)
            continue

        grams, reason = units.convert_to_grams(
            qty=row.get("qty_numeric"),
            unit_std=row.get("unit_std"),
            food_name_std=row.get("food_name_std", ""),
            grams_per_cup_map=grams_per_cup_map,
            grams_per_count_map=grams_per_count_map,
            default_density_g_per_ml=default_density_g_per_ml,
        )
        if grams is None or grams <= 0:
            grams = 100.0
            final_reason = existing_reason or reason or "no_direct_conversion"
            assumed_flags.append(True)
        else:
            final_reason = existing_reason or reason
            assumed_flags.append(False)
        grams_list.append(grams)
        reason_list.append(final_reason or existing_reason)

    df["grams_final"] = grams_list
    df["assumption_reason"] = reason_list
    df["assumed_100g_flag"] = assumed_flags
    return df


def compute_item_macros(items_with_grams: pd.DataFrame) -> pd.DataFrame:
    """Derive calories/macros per item using per-100g nutrient data."""
    df = items_with_grams.copy()
    for macro_col, nutrient_col in [
        ("calories", "calories_kcal_100g"),
        ("carbs_g", "carbs_g_100g"),
        ("protein_g", "protein_g_100g"),
        ("fat_g", "fat_g_100g"),
    ]:
        df[macro_col] = df["grams_final"] * df[nutrient_col] / 100.0
    return df


def build_meal_features(items: pd.DataFrame, clean_events: pd.DataFrame) -> pd.DataFrame:
    """Aggregate item-level data to meal-level features with temporal context."""
    agg = items.groupby("meal_id").agg(
        calories=("calories", "sum"),
        carbs_g=("carbs_g", "sum"),
        protein_g=("protein_g", "sum"),
        fat_g=("fat_g", "sum"),
        items_count=("food_text_raw", "count"),
        assumed_items_count=("assumed_100g_flag", "sum"),
        missing_usda_items_count=("usda_match_status", lambda s: (s == "missing").sum()),
    )
    agg.reset_index(inplace=True)

    events = clean_events[["meal_id", "datetime", "date", "blood_glucose_2hrs_post", "cbg_post", "meal_type"]].drop_duplicates("meal_id")
    meal_features = agg.merge(events, on="meal_id", how="left")

    meal_features["hour"] = meal_features["datetime"].dt.hour
    meal_features["hour_sin"] = np.sin(2 * np.pi * meal_features["hour"] / 24)
    meal_features["hour_cos"] = np.cos(2 * np.pi * meal_features["hour"] / 24)

    meal_features.sort_values(["date", "datetime"], inplace=True)
    meal_features["time_since_prev_meal_same_day_min"] = (
        meal_features.groupby("date")["datetime"].diff().dt.total_seconds() / 60.0
    )
    return meal_features


def build_model_table(meal_features: pd.DataFrame) -> pd.DataFrame:
    from .lag_features import add_same_day_lag

    model = meal_features.copy()
    model = add_same_day_lag(model)
    return model


def build_assumptions_report(items: pd.DataFrame) -> pd.DataFrame:
    """Summarize rows where assumptions/defaults were applied for auditing."""
    columns = [
        "event_id",
        "meal_id",
        "datetime",
        "date",
        "event_type",
        "food_text_raw",
        "food_name_std",
        "food_category",
        "qty_raw",
        "unit_raw",
        "qty_numeric",
        "unit_std",
        "grams_final",
        "assumed_100g_flag",
        "default_applied_flag",
        "assumption_reason",
        "usda_match_status",
        "fdcId",
        "usda_description",
    ]
    present_cols = [c for c in columns if c in items.columns]
    report = items.loc[items["assumed_100g_flag"] | items.get("default_applied_flag", False), present_cols]
    report.sort_values(["date", "datetime", "meal_id"], inplace=True)
    return report


def build_manual_review(items: pd.DataFrame) -> pd.DataFrame:
    """List items missing USDA matches or relying on assumptions for QA review."""
    mask = (items["usda_match_status"] == "missing") | (items["assumed_100g_flag"])
    review = items.loc[mask, ["food_text_raw", "food_name_std", "meal_id", "event_id", "assumption_reason", "usda_match_status"]]
    review.sort_values(["usda_match_status", "assumption_reason"], inplace=True)
    return review
