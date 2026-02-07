"""Lag feature utilities for meal-to-glucose modeling."""
from __future__ import annotations

import pandas as pd


def add_same_day_lag(meal_features: pd.DataFrame) -> pd.DataFrame:
    df = meal_features.copy()
    df.sort_values(["date", "datetime"], inplace=True)
    if "blood_glucose_2hrs_post" not in df.columns:
        df["blood_glucose_2hrs_post"] = df.get("cbg_post")
    df["cbg_post"] = df.get("cbg_post", df["blood_glucose_2hrs_post"])
    df["cbg_prev_same_day"] = df.groupby("date")["blood_glucose_2hrs_post"].shift(1)
    return df
