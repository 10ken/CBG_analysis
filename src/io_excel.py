"""Excel ingestion and normalization utilities for CBG meal/CBG logs.

Converts heterogeneous meal logs (multiple sheets, mixed schemas) into a
normalized events table that preserves meal labels, post-meal glucose values,
and timestamps for downstream meal parsing and modeling.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

from . import clean_schema_dec25_jan26, clean_schema_feb2026
from .clean_common import parse_bg_value, parse_datetime


def _finalize_events(frames: List[pd.DataFrame]) -> pd.DataFrame:
    combined = pd.concat(frames, ignore_index=True)
    combined["sheet_source"] = combined["sheet_source"].astype(str)
    combined["sheet"] = combined["sheet_source"]
    combined["date_raw"] = combined["date_raw"].astype(str)
    combined["time_raw"] = combined["time_raw"].astype(str)
    combined["datetime"] = parse_datetime(combined["date_raw"], combined["time_raw"])

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
    combined["blood_glucose_2hrs_post"] = combined["bg_2hrs_post_raw"].apply(parse_bg_value)
    combined["cbg_post"] = combined["blood_glucose_2hrs_post"]
    combined.sort_values(["date", "datetime", "row_in_sheet"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    combined["meal_id"] = np.arange(1, len(combined) + 1)
    return combined


def load_clean_events(excel_path: Path) -> pd.DataFrame:
    """Load Excel and normalize into a unified event table."""

    raw = pd.read_excel(excel_path, sheet_name=None)
    frames: List[pd.DataFrame] = []
    for sheet_name, df in raw.items():
        if clean_schema_dec25_jan26.is_schema(df):
            frames.append(clean_schema_dec25_jan26.clean(df, sheet_name))
        elif clean_schema_feb2026.is_schema(df):
            frames.append(clean_schema_feb2026.clean(df, sheet_name))
        else:
            raise ValueError(f"Unknown schema for sheet {sheet_name}; columns={list(df.columns)}")

    combined = _finalize_events(frames)
    return combined
