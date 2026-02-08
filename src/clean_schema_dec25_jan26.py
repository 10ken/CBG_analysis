"""Cleaner for the "Dec25 Jan26" sheet schema.

Handles the Dec25/Jan26 layout only; shared helpers live in clean_common.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .clean_common import (
    NORMALIZED_EVENT_COLUMNS,
    assign_event_and_meal_type,
    ensure_required_columns,
    stable_event_id,
)


REQUIRED_COLUMNS = [
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


def is_schema(df: pd.DataFrame) -> bool:
    return set(REQUIRED_COLUMNS).issubset(df.columns)


def clean(df: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
    ensure_required_columns(df, REQUIRED_COLUMNS, sheet_name)
    out = df.copy()
    out["event_id"] = [stable_event_id(sheet_name, i) for i in range(len(out))]
    out["sheet_source"] = str(sheet_name)
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
    out = assign_event_and_meal_type(out, meal_label_col="meal_label")
    return out[NORMALIZED_EVENT_COLUMNS]