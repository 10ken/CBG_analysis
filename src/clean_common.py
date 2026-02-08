"""Shared helpers for schema-specific Excel cleaners.

Provides reusable utilities for event id generation, meal type inference,
datetime parsing, and basic validation used by per-sheet cleaning modules.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, List

import numpy as np
import pandas as pd


# Standardized column ordering for cleaned events coming out of schema cleaners.
NORMALIZED_EVENT_COLUMNS: List[str] = [
    "event_id",
    "row_in_sheet",
    "sheet_source",
    "event_type",
    "meal_label",
    "meal_type",
    "date_raw",
    "time_raw",
    "bg_2hrs_post_raw",
    "protein_text",
    "carb_text",
    "veggies_text",
    "fat_text",
    "other_text",
    "items_text",
    "notes",
]


def ensure_required_columns(df: pd.DataFrame, required: Iterable[str], sheet_name: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Sheet {sheet_name} missing columns: {missing}")


def stable_event_id(sheet: str, row_index: int) -> str:
    token = f"{sheet}-{row_index}".encode()
    return hashlib.sha1(token).hexdigest()[:12]


def parse_bg_value(value) -> float:
    """Parse BG 2 HRS POST cell into a numeric value or NaN."""
    if value is None:
        return np.nan
    if isinstance(value, float) or isinstance(value, int):
        return float(value)
    text = str(value)
    if not text.strip():
        return np.nan
    nums = re.findall(r"[-+]?[0-9]*\.?[0-9]+", text)
    if not nums:
        return np.nan
    vals = [float(n) for n in nums]
    if len(vals) == 1:
        return vals[0]
    return float(np.mean(vals[:2]))


def normalize_meal_type(label: str) -> str | None:
    if label is None:
        return None
    text = str(label).strip().lower()
    if not text:
        return None
    if "fast" in text:
        return "fasting"
    meal_keywords = [
        ("breakfast", "breakfast"),
        ("brunch", "breakfast"),
        ("lunch", "lunch"),
        ("dinner", "dinner"),
        ("supper", "dinner"),
        ("snack", "snack"),
    ]
    for key, canonical in meal_keywords:
        if key in text:
            return canonical
    return None


def assign_event_and_meal_type(df: pd.DataFrame, meal_label_col: str = "meal_label") -> pd.DataFrame:
    df = df.copy()
    df["event_type"] = df[meal_label_col].str.contains("fast", case=False, na=False)
    df["event_type"] = df["event_type"].map({True: "fasting", False: "meal"})
    df["meal_type"] = df[meal_label_col].apply(normalize_meal_type)
    df.loc[df["event_type"] == "fasting", "meal_type"] = "fasting"
    return df


def parse_datetime(date_series: pd.Series, time_series: pd.Series) -> pd.Series:
    date_parsed = pd.to_datetime(date_series, errors="coerce")
    dt = pd.to_datetime(date_parsed.astype(str) + " " + time_series.astype(str), errors="coerce")
    dt = dt.fillna(pd.to_datetime(date_series, errors="coerce"))
    return dt