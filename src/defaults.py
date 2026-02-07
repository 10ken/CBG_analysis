"""Default quantity/unit rules for foods in the CBG meal log pipeline.

Loads a declarative defaults config and applies it to item-level rows so no
logged meal item proceeds without a quantity/unit or grams fallback—helpful
when free-text entries omit quantities but still need macro estimates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml
import pandas as pd


@dataclass
class DefaultRule:
    name: str
    match_type: str
    pattern: str
    default_qty: Optional[float]
    default_unit: Optional[str]
    default_grams_override: Optional[float]
    reason: str

    def matches(self, food_name_std: str) -> bool:
        if self.match_type == "exact":
            return food_name_std == self.pattern
        if self.match_type == "regex":
            return re.search(self.pattern, food_name_std) is not None
        return False


COUNTABLE_FOODS = {
    "egg",
    "eggs",
    "olive",
    "olives",
    "pretzel",
    "pretzels",
    "kiwi",
    "pizza",
    "bread",
    "wonton",
    "dumpling",
    "roll",
}


def load_defaults_config(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_rules(cfg: Dict) -> List[DefaultRule]:
    rules = []
    for entry in cfg.get("rules", []):
        rules.append(
            DefaultRule(
                name=entry.get("name", entry.get("pattern", "rule")),
                match_type=entry.get("match_type", "exact"),
                pattern=entry.get("pattern", ""),
                default_qty=entry.get("default_qty"),
                default_unit=entry.get("default_unit"),
                default_grams_override=entry.get("default_grams_override"),
                reason=entry.get("reason", "default_applied"),
            )
        )
    return rules


def apply_default_rules(
    items: pd.DataFrame,
    cfg: Dict,
    grams_per_count_map: Optional[Dict[str, float]] = None,
    **_: Dict,
) -> pd.DataFrame:
    """Apply defaults to rows missing qty/unit or grams override.

    Adds/updates columns: qty_numeric, unit_std, grams_override,
    default_applied_flag, assumption_reason.
    """
    rules = parse_rules(cfg)
    global_default = cfg.get("global_default", {})
    items = items.copy()

    grams_per_count_map = grams_per_count_map or {}

    def _apply(row):
        food_name = row.get("food_name_std") or ""
        if pd.isna(food_name):
            food_name = ""

        qty_present = pd.notnull(row.get("qty_numeric"))
        unit_present = bool(row.get("unit_std"))

        # Flax/hemp missing qty: default to 3 tsp
        if ("flax" in food_name or "hemp" in food_name) and not qty_present:
            row["qty_numeric"] = 3.0
            row["unit_std"] = row.get("unit_std") or "tsp"
            row["default_applied_flag"] = True
            row["assumption_reason"] = row.get("assumption_reason") or "default_flax_hemp_3tsp"
            qty_present = True
            unit_present = bool(row.get("unit_std"))

        # If qty exists but unit is missing, infer cup/count with explicit reasons.
        if qty_present and not unit_present:
            countable = food_name in COUNTABLE_FOODS or food_name in grams_per_count_map
            if countable:
                row["unit_std"] = "count"
                row["default_applied_flag"] = True
                row["assumption_reason"] = row.get("assumption_reason") or "inferred_unit_count_missing_unit"
                return row
            if row.get("qty_numeric") is not None and row.get("qty_numeric") < 1:
                row["unit_std"] = "cup"
                row["default_applied_flag"] = True
                row["assumption_reason"] = row.get("assumption_reason") or "inferred_unit_cup_missing_unit"
                return row
            row["unit_std"] = "cup"
            row["default_applied_flag"] = True
            row["assumption_reason"] = row.get("assumption_reason") or "inferred_unit_cup_missing_unit"
            return row

        # If both qty and unit already present, nothing to do.
        if qty_present and unit_present:
            return row

        needs_qty = not qty_present
        needs_unit = not unit_present
        needs_override = pd.isna(row.get("grams_override"))
        if not (needs_qty or needs_unit or needs_override):
            return row

        applied_rule: Optional[DefaultRule] = None
        for rule in rules:
            if rule.matches(food_name):
                applied_rule = rule
                break

        def _set_if_missing(field: str, value):
            if value is None:
                return False
            if pd.isna(row.get(field)) or row.get(field) in [None, ""]:
                row[field] = value
                return True
            return False

        applied = False
        reason = None

        if applied_rule is None:
            applied |= _set_if_missing("qty_numeric", global_default.get("default_qty"))
            applied |= _set_if_missing("unit_std", global_default.get("default_unit"))
            applied |= _set_if_missing("grams_override", global_default.get("default_grams_override"))
            reason = global_default.get("reason", "default_missing_qty_unit_100g")
        else:
            applied |= _set_if_missing("qty_numeric", applied_rule.default_qty)
            applied |= _set_if_missing("unit_std", applied_rule.default_unit)
            applied |= _set_if_missing("grams_override", applied_rule.default_grams_override)
            reason = applied_rule.reason

        if applied:
            row["default_applied_flag"] = True
            row["assumption_reason"] = reason
        return row

    if "default_applied_flag" not in items.columns:
        items["default_applied_flag"] = False
    if "assumption_reason" not in items.columns:
        items["assumption_reason"] = None
    if "grams_override" not in items.columns:
        items["grams_override"] = None

    items = items.apply(_apply, axis=1)
    return items
