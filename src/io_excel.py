"""Excel ingestion and normalization utilities for CBG meal/CBG logs.

Converts heterogeneous meal logs (multiple sheets, mixed schemas) into a
normalized events table that preserves meal labels, post-meal glucose values,
and timestamps for downstream meal parsing and modeling.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import hashlib
import re

import numpy as np
import pandas as pd


MEAL_TYPE_KEYWORDS = [
    ("breakfast", "breakfast"),
    ("brunch", "breakfast"),
    ("lunch", "lunch"),
    ("dinner", "dinner"),
    ("supper", "dinner"),
    ("snack", "snack"),
]


def _parse_bg_value(value) -> float:
    """Parse BG 2 HRS POST cell into a numeric value or NaN.

    - Empty -> NaN
    - Single number -> that value
    - Multiple numbers (e.g., "6.4 (CGM 5.8)") -> average of the numbers
    """
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
    for key, canonical in MEAL_TYPE_KEYWORDS:
        if key in text:
            return canonical
    return None


REQUIRED_DEC25_COLS = [
    "Date",
    "Time",
    "Meal",
    "BG 2 HRS POST",
    "Protein",
    "Carb",
    "Veggies",
    "Fat",
    "Other",
]

REQUIRED_FEB_COLS = ["DATE", "TIME", "MEAL", "BG 2 HRS POST", "ITEMS", "NOTES"]


def _stable_event_id(sheet: str, row_index: int) -> str:
    token = f"{sheet}-{row_index}".encode()
    return hashlib.sha1(token).hexdigest()[:12]


def _parse_datetime(date_series: pd.Series, time_series: pd.Series) -> pd.Series:
    date_parsed = pd.to_datetime(date_series, errors="coerce")
    time_parsed = pd.to_datetime(time_series, errors="coerce").dt.time
    dt = pd.to_datetime(
        date_parsed.astype(str) + " " + time_series.astype(str), errors="coerce"
    )
    # fallback when time is NaT
    dt = dt.fillna(pd.to_datetime(date_series, errors="coerce"))
    return dt


def _normalize_dec25(df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    missing = [c for c in REQUIRED_DEC25_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Sheet {sheet_name} missing columns: {missing}")
    out = df.copy()
    out["event_id"] = [
        _stable_event_id(sheet_name, i) for i in range(len(out))
    ]
    out["sheet"] = sheet_name
    out.rename(
        columns={
            "Date": "date_raw",
            "Time": "time_raw",
            "Meal": "meal_label",
            "BG 2 HRS POST": "bg_2hrs_post_raw",
            "Protein": "protein_text",
            "Carb": "carb_text",
            "Veggies": "veggies_text",
            "Fat": "fat_text",
            "Other": "other_text",
        },
        inplace=True,
    )
    out["items_text"] = None
    out["notes"] = None
    out["row_in_sheet"] = np.arange(len(out))
    out["event_type"] = out["meal_label"].str.contains("fast", case=False, na=False)
    out["event_type"] = out["event_type"].map({True: "fasting", False: "meal"})
    out["meal_type"] = out["meal_label"].apply(normalize_meal_type)
    out.loc[out["event_type"] == "fasting", "meal_type"] = "fasting"
    return out[
        [
            "event_id",
            "row_in_sheet",
            "sheet",
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
    ]


def _normalize_feb(df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    missing = [c for c in REQUIRED_FEB_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Sheet {sheet_name} missing columns: {missing}")
    out = df.copy()
    out["event_id"] = [
        _stable_event_id(sheet_name, i) for i in range(len(out))
    ]
    out["sheet"] = sheet_name
    out.rename(
        columns={
            "DATE": "date_raw",
            "TIME": "time_raw",
            "MEAL": "meal_label",
            "BG 2 HRS POST": "bg_2hrs_post_raw",
            "ITEMS": "items_text",
            "NOTES": "notes",
        },
        inplace=True,
    )
    out["protein_text"] = None
    out["carb_text"] = None
    out["veggies_text"] = None
    out["fat_text"] = None
    out["other_text"] = None
    out["row_in_sheet"] = np.arange(len(out))
    out["event_type"] = out["meal_label"].str.contains("fast", case=False, na=False)
    out["event_type"] = out["event_type"].map({True: "fasting", False: "meal"})
    out["meal_type"] = out["meal_label"].apply(normalize_meal_type)
    out.loc[out["event_type"] == "fasting", "meal_type"] = "fasting"
    return out[
        [
            "event_id",
            "row_in_sheet",
            "sheet",
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
    ]


def load_clean_events(excel_path: Path) -> pd.DataFrame:
    """Load Excel and normalize into a unified event table."""
    raw = pd.read_excel(excel_path, sheet_name=None)
    frames: List[pd.DataFrame] = []
    for sheet_name, df in raw.items():
        if set(REQUIRED_DEC25_COLS).issubset(df.columns):
            frames.append(_normalize_dec25(df, sheet_name))
        elif set(REQUIRED_FEB_COLS).issubset(df.columns):
            frames.append(_normalize_feb(df, sheet_name))
        else:
            raise ValueError(f"Unknown schema for sheet {sheet_name}")
    combined = pd.concat(frames, ignore_index=True)
    combined["date_raw"] = combined["date_raw"].astype(str)
    combined["time_raw"] = combined["time_raw"].astype(str)
    combined["datetime"] = _parse_datetime(
        combined["date_raw"], combined["time_raw"]
    )
    missing_dt_mask = combined["datetime"].isna()
    combined["datetime_imputed_flag"] = missing_dt_mask
    if missing_dt_mask.any():
        combined["datetime"] = combined["datetime"].ffill()
        combined.loc[combined["datetime"].isna(), "datetime"] = combined["datetime"].bfill()
        if combined["datetime"].isna().any():
            fallback_ts = combined["datetime"].dropna().min()
            if pd.isna(fallback_ts):
                fallback_ts = pd.Timestamp("1970-01-01")
            combined.loc[combined["datetime"].isna(), "datetime"] = fallback_ts
        combined.loc[missing_dt_mask, "datetime_imputed_flag"] = True
    combined["date"] = combined["datetime"].dt.date
    combined["blood_glucose_2hrs_post"] = combined["bg_2hrs_post_raw"].apply(_parse_bg_value)
    combined["cbg_post"] = combined["blood_glucose_2hrs_post"]
    combined.sort_values(["date", "datetime", "row_in_sheet"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    combined["meal_id"] = np.arange(1, len(combined) + 1)
    return combined
