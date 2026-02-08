"""Food parsing utilities for the CBG meal logs.

Explodes meal-category text into item-level rows with parsed quantities/units
so we can estimate macros per item before aggregating to meals and linking to
glucose responses.
"""
from __future__ import annotations

import re
from typing import List

import numpy as np
import pandas as pd

from .units import ParsedQuantity, UNIT_SYNONYMS, normalize_food_name, parse_quantity_unit

CATEGORY_COLS = ["protein_text", "carb_text", "veggies_text", "fat_text", "other_text"]


def _split_items(cell: str) -> List[str]:
    if cell is None or (isinstance(cell, float) and np.isnan(cell)):
        return []
    text = str(cell)
    # If the entry starts with a quantity, keep slashes for compound expansion; otherwise treat slashes
    # that are not numeric fractions as delimiters between foods.
    starts_with_qty = bool(re.match(r"^\s*(\d+\s\d/\d|\d+/\d|\d*\.\d+|\d+)", text))
    if not starts_with_qty:
        text = re.sub(r"(?<!\d)/(?!\d)", "|", text)
    parts = re.split(r"[\n;,\|]+", text)
    return [p.strip() for p in parts if p.strip()]


def _expand_compound_item(item: str) -> List[str]:
    """Expand compound slash-delimited foods while preserving auditability."""
    raw = item.strip()
    if not raw:
        return []

    # Explicit special-case: skyr/blueberries/flax/hemp/kiwi with qty+unit on skyr, fixed defaults on rest
    lower_raw = raw.lower()
    lower_raw = lower_raw.replace("bluberries", "blueberries")
    if re.match(r"^\s*1/4\s+[^/]*yogurt/blueberries", lower_raw):
        return ["0.25 cup yogurt", "100 g blueberries"]

    if all(keyword in lower_raw for keyword in ["skyr", "blueberries", "flax", "hemp", "chia"]):
        apple_part = None
        if "apple" in lower_raw:
            apple_part = "0.25 cup apple" if "1/4" in lower_raw else "apple"
        expanded = [
            "0.24 cup skyr yogurt",
            "100 g blueberries",
            "3 tsp flax seed",
            "3 tsp hempseed",
            "3 tsp chia seeds",
        ]
        if apple_part:
            expanded.append(apple_part)
        return [p for p in expanded if p]

    if "skyr/blueberries/flax/hemp/kiwi" in lower_raw:
        pq = parse_quantity_unit(raw)
        skyr_part = raw
        if pq.qty_numeric is not None and pq.unit_std:
            skyr_part = f"{pq.qty_numeric} {pq.unit_std} skyr"
        elif pq.qty_numeric is not None:
            skyr_part = f"{pq.qty_numeric} skyr"
        expanded = [
            skyr_part,
            "100 g blueberries",
            "3 tsp flax seed",
            "3 tsp hempseed",
            "1 count kiwi",
        ]
        return [p for p in expanded if p]

    unit_vocab = "|".join(map(re.escape, sorted(UNIT_SYNONYMS.keys(), key=len, reverse=True)))
    qty_match = re.match(
        rf"^\s*(?P<qty>\d+\s\d/\d|\d+/\d|\d*\.\d+|\d+)(?:\s*(?P<unit>{unit_vocab}))?\s*(?P<foods>.+)$",
        raw,
        flags=re.IGNORECASE,
    )
    if not qty_match:
        return [raw]

    qty_raw = qty_match.group("qty")
    unit_raw = qty_match.group("unit") or ""
    foods_part = qty_match.group("foods")

    pq = parse_quantity_unit(f"{qty_raw} {unit_raw}".strip())
    qty_numeric = pq.qty_numeric
    unit_std = pq.unit_std if pq.unit_std != "count" else pq.unit_std

    foods = [f.strip() for f in foods_part.split("/") if f.strip()]
    if qty_numeric is None or unit_std is None or len(foods) == 0:
        return [raw]

    if len(foods) == 1:
        return [raw]

    expanded: List[str] = []
    if len(foods) == 2:
        share = qty_numeric / 2.0
        base_prefix = foods[0].split()[0]
        for idx, fpart in enumerate(foods):
            name = fpart
            if idx > 0 and base_prefix not in fpart.split():
                name = f"{base_prefix} {fpart}"
            expanded.append(f"{share} {unit_std} {name}")
        return expanded

    # More than two foods: qty applies to first, others become separate items (no qty/unit)
    expanded.append(f"{qty_numeric} {unit_std} {foods[0]}")
    expanded.extend(foods[1:])
    return expanded


def explode_food_items(clean_events: pd.DataFrame) -> pd.DataFrame:
    """Explode the clean events table into item-level rows."""
    records = []
    for _, row in clean_events.iterrows():
        base = {
            "event_id": row["event_id"],
            "meal_id": row["meal_id"],
            "sheet_source": row.get("sheet_source"),
            "event_type": row["event_type"],
            "meal_type": row.get("meal_type"),
            "datetime": row["datetime"],
            "date": row["date"],
            "meal_label": row["meal_label"],
            "blood_glucose_2hrs_post": row.get("blood_glucose_2hrs_post"),
            "cbg_post": row.get("blood_glucose_2hrs_post"),
        }
        row_records = []

        # Category-specific columns
        for col in CATEGORY_COLS:
            for item in _split_items(row.get(col)):
                for expanded in _expand_compound_item(item):
                    pq: ParsedQuantity = parse_quantity_unit(expanded)
                    rec = {
                        **base,
                        "food_category": col.replace("_text", ""),
                        "food_text_raw": expanded,
                        "qty_raw": pq.qty_raw,
                        "unit_raw": pq.unit_raw,
                        "qty_numeric": pq.qty_numeric,
                        "unit_std": pq.unit_std,
                        "food_name_std": pq.food_name_std,
                    }
                    row_records.append(rec)

        # Items column from Feb sheet
        if row.get("items_text"):
            for item in _split_items(row.get("items_text")):
                for expanded in _expand_compound_item(item):
                    pq: ParsedQuantity = parse_quantity_unit(expanded)
                    rec = {
                        **base,
                        "food_category": "unspecified",
                        "food_text_raw": expanded,
                        "qty_raw": pq.qty_raw,
                        "unit_raw": pq.unit_raw,
                        "qty_numeric": pq.qty_numeric,
                        "unit_std": pq.unit_std,
                        "food_name_std": pq.food_name_std,
                    }
                    row_records.append(rec)

        # If no items and fasting, retain a fasting record for this event
        if not row_records and row["event_type"] == "fasting":
            rec = {**base, "food_category": "fasting", "food_text_raw": "fasting"}
            row_records.append(rec)

        records.extend(row_records)

    items = pd.DataFrame(records)
    if items.empty:
        return items
    items.sort_values(["date", "datetime", "meal_id", "food_category"], inplace=True)
    items.reset_index(drop=True, inplace=True)
    return items
