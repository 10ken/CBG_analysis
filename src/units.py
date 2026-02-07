"""Unit parsing and conversion helpers for meal-to-glucose analysis.

Centralizes quantity/unit parsing and conversion to grams so free-text meal
entries can be standardized before estimating macros and linking to glucose
responses. Fractions and mixed numbers are supported, and unknown conversions
return ``None`` so callers can apply assumptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import yaml

# Canonical cup volume used across conversions
CUP_ML = 240.0

# Canonical unit conversions; all synonyms map to these keys via UNIT_SYNONYMS
MASS_UNITS: Dict[str, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "oz": 28.3495,
    "lb": 453.592,
}

VOLUME_UNITS_ML: Dict[str, float] = {
    "tsp": 4.92892,
    "tbsp": 14.7868,
    "cup": CUP_ML,
    "ml": 1.0,
    "l": 1000.0,
    "fl oz": 29.5735,
}

UNIT_SYNONYMS: Dict[str, str] = {
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "mg": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "oz": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "pound": "lb",
    "pounds": "lb",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "cup": "cup",
    "cups": "cup",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "fl": "fl oz",
    "floz": "fl oz",
    "fl oz": "fl oz",
    "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    "count": "count",
    "piece": "count",
    "pieces": "count",
    "pc": "count",
    "each": "count",
    "slice": "count",
    "slices": "count",
    "roll": "count",
    "rolls": "count",
    "wonton": "count",
    "wontons": "count",
    "dumpling": "count",
    "dumplings": "count",
}

# Optional grams-per-count for discrete foods; overridable via config
GRAMS_PER_COUNT: Dict[str, float] = {
    "egg": 50.0,
    "eggs": 50.0,
    "olive": 5.0,
    "olives": 5.0,
    "kiwi": 75.0,
    "bread": 28.0,
    "pizza": 125.0,
    "wonton": 20.0,
    "dumpling": 25.0,
    "roll": 40.0,
}

# Food-specific grams-per-cup overrides; overridable via config
GRAMS_PER_CUP: Dict[str, float] = {
    "blueberries": 148.0,
    "flax seed": 135.0,
    "hempseed": 130.0,
    "vegetables": 150.0,
    "mixed nuts": 150.0,
}

# Volume density fallback when no food-specific mapping is available
DEFAULT_DENSITY_G_PER_ML = 1.0

STOPWORDS = {"of", "in", "the", "a", "an"}

MEASUREMENT_WORDS = {
    "cup",
    "cups",
    "tsp",
    "tbsp",
    "tablespoon",
    "tablespoons",
    "teaspoon",
    "teaspoons",
    "oz",
    "ounce",
    "ounces",
    "g",
    "gram",
    "grams",
    "kg",
    "lb",
    "pound",
    "pounds",
    "ml",
    "milliliter",
    "milliliters",
    "l",
    "liter",
    "liters",
    "slice",
    "slices",
    "count",
    "piece",
    "pieces",
    "fl",
    "fl oz",
    "floz",
}


def _text_replacements_path(default_path: Path | None = None) -> Path:
    if default_path is not None:
        return default_path
    return Path(__file__).resolve().parents[1] / "config" / "text_replacements.yaml"


@lru_cache(maxsize=1)
def load_text_replacements(config_path: Path | None = None) -> Dict[str, str]:
    path = _text_replacements_path(config_path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    replacements = data.get("replacements") or {}
    cleaned = {}
    for k, v in replacements.items():
        if k is None:
            continue
        cleaned[str(k).strip().lower()] = str(v).strip().lower()
    return cleaned


def normalize_food_text_for_usda(
    text: str,
    replacements: Optional[Dict[str, str]] = None,
    remove_measurement_words: bool = True,
) -> str:
    """Normalize free-text food names for USDA queries.

    - Strip parenthetical content
    - Lowercase
    - Replace underscores with spaces
    - Apply word-boundary replacements (config-driven)
    - Remove stopwords and optional measurement words
    - Collapse whitespace and trim
    """

    raw = (text or "").replace("_", " ")
    cleaned = strip_parentheses(raw).lower()
    cleaned = normalize_whitespace(cleaned)

    replacements = replacements or load_text_replacements()
    if replacements:
        for src, target in replacements.items():
            pattern = rf"\b{re.escape(src)}\b"
            cleaned = re.sub(pattern, target, cleaned)
    cleaned = normalize_whitespace(cleaned)

    tokens = cleaned.split()
    banned = set(STOPWORDS)
    if remove_measurement_words:
        banned |= set(MEASUREMENT_WORDS)
    tokens = [tok for tok in tokens if tok not in banned]
    cleaned = " ".join(tokens)
    return normalize_whitespace(cleaned)


@dataclass
class ParsedQuantity:
    qty_raw: Optional[str]
    unit_raw: Optional[str]
    qty_numeric: Optional[float]
    unit_std: Optional[str]
    food_name_std: str
    parse_notes: Optional[str] = None


def normalize_food_name(text: str) -> str:
    """Public helper to normalize food names (backward compatibility)."""
    return normalize_food_text_for_usda(text, remove_measurement_words=True)


def strip_parentheses(text: str) -> str:
    return re.sub(r"\([^)]*\)", " ", text)


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def insert_space_number_word(text: str) -> str:
    return re.sub(r"(?P<num>\d)(?P<word>[A-Za-z])", r"\g<num> \g<word>", text)


def fix_split_fractions(text: str) -> str:
    match = re.match(r"^\s*(?P<num>\d+)\s+(?P<den>\d+)(?P<rest>\b.*)$", text)
    if not match:
        return text
    denominator = int(match.group("den"))
    if denominator == 0 or denominator > 16:
        return text
    return f"{match.group('num')}/{match.group('den')}{match.group('rest')}"


def _parse_fraction_token(text: str) -> Optional[float]:
    if "/" in text:
        try:
            num, den = text.split("/", 1)
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_fraction(text: str) -> Optional[float]:
    """Public fraction parser for backward compatibility."""
    text = (text or "").strip()
    if not text:
        return None
    if " " in text:
        total = 0.0
        for part in text.split():
            val = _parse_fraction_token(part)
            if val is None:
                return None
            total += val
        return total
    return _parse_fraction_token(text)


def _parse_leading_quantity(tokens: Iterable[str]) -> Tuple[Optional[str], Optional[float], int]:
    tok_list = list(tokens)
    if not tok_list:
        return None, None, 0

    qty_raw = None
    qty_numeric = None
    consumed = 0

    # Mixed number e.g., 1 1/2
    if len(tok_list) >= 2 and re.match(r"^\d+$", tok_list[0]) and re.match(r"^\d+/\d+$", tok_list[1]):
        first = float(tok_list[0])
        frac = _parse_fraction_token(tok_list[1])
        if frac is not None:
            qty_raw = f"{tok_list[0]} {tok_list[1]}"
            qty_numeric = first + frac
            consumed = 2
            return qty_raw, qty_numeric, consumed

    # Fraction only
    if re.match(r"^\d+/\d+$", tok_list[0]):
        qty_raw = tok_list[0]
        qty_numeric = _parse_fraction_token(tok_list[0])
        consumed = 1
        return qty_raw, qty_numeric, consumed

    # Decimal or integer
    if re.match(r"^\d*(?:\.\d+)?$", tok_list[0]) and tok_list[0].strip() != "":
        qty_raw = tok_list[0]
        qty_numeric = _parse_fraction_token(tok_list[0])
        consumed = 1
        return qty_raw, qty_numeric, consumed

    return None, None, 0


def _find_unit(tokens: list[str], start_idx: int = 0) -> Tuple[Optional[str], Optional[str], int]:
    unit_raw = None
    unit_std = None
    consumed = 0

    if start_idx >= len(tokens):
        return unit_raw, unit_std, consumed

    # Prefer two-token units like "fl oz"
    if start_idx + 1 < len(tokens):
        cand_two = f"{tokens[start_idx]} {tokens[start_idx + 1]}".lower()
        if cand_two in UNIT_SYNONYMS:
            unit_raw = cand_two
            unit_std = UNIT_SYNONYMS[cand_two]
            consumed = 2
            return unit_raw, unit_std, consumed

    cand_one = tokens[start_idx].lower()
    if cand_one in UNIT_SYNONYMS:
        unit_raw = cand_one
        unit_std = UNIT_SYNONYMS[cand_one]
        consumed = 1
    return unit_raw, unit_std, consumed


def _remove_stopwords(tokens: list[str]) -> list[str]:
    unit_words = set(UNIT_SYNONYMS.keys()) | set(UNIT_SYNONYMS.values())
    return [t for t in tokens if t not in STOPWORDS and t not in unit_words]


def _apply_replacements(text: str, replacements: Optional[Dict[str, str]] = None) -> str:
    out = text
    replacements = replacements or load_text_replacements()
    for src, target in replacements.items():
        pattern = rf"\b{re.escape(src)}\b"
        out = re.sub(pattern, target, out)
    return out


def _fix_broccoli_spellings(tokens: list[str]) -> list[str]:
    fixed = []
    for tok in tokens:
        if SequenceMatcher(None, tok, "broccoli").ratio() >= 0.9:
            fixed.append("broccoli")
        else:
            fixed.append(tok)
    return fixed


def parse_quantity_unit(food_text_raw: str, replacements: Optional[Dict[str, str]] = None) -> ParsedQuantity:
    text = strip_parentheses(food_text_raw or "")
    text = insert_space_number_word(text)
    text = fix_split_fractions(text)
    text = normalize_whitespace(text)
    tokens = text.split()

    qty_raw, qty_numeric, consumed_qty = _parse_leading_quantity(tokens)
    remaining_tokens = tokens[consumed_qty:]

    unit_raw = None
    unit_std = None
    consumed_unit = 0
    if qty_raw is not None:
        unit_raw, unit_std, consumed_unit = _find_unit(remaining_tokens, 0)

    remaining_tokens = remaining_tokens[consumed_unit:]
    remaining_tokens = _remove_stopwords([t.lower() for t in remaining_tokens])

    food_tokens = [t for t in remaining_tokens if not re.match(r"^\d|\d/\d", t)]
    food_tokens = _fix_broccoli_spellings(food_tokens)
    food_text = " ".join(food_tokens)
    food_text = normalize_food_text_for_usda(food_text, replacements=replacements, remove_measurement_words=True)

    return ParsedQuantity(
        qty_raw=qty_raw,
        unit_raw=unit_raw,
        qty_numeric=qty_numeric,
        unit_std=unit_std,
        food_name_std=food_text,
    )


def convert_to_grams(
    qty: Optional[float],
    unit_std: Optional[str],
    food_name_std: str,
    grams_per_cup_map: Optional[Dict[str, float]] = None,
    grams_per_count_map: Optional[Dict[str, float]] = None,
    default_density_g_per_ml: float = DEFAULT_DENSITY_G_PER_ML,
) -> Tuple[Optional[float], Optional[str]]:
    """Convert a quantity/unit pair to grams.

    Returns (grams, assumption_reason). If conversion is not possible, grams is
    None and a reason string explains why so the caller can decide how to
    default.
    """
    grams_per_cup_map = grams_per_cup_map or {}
    grams_per_count_map = grams_per_count_map or {}
    if unit_std in UNIT_SYNONYMS:
        unit_std = UNIT_SYNONYMS[unit_std]
    if qty is None or unit_std is None:
        return None, "missing_qty_or_unit"

    if unit_std in MASS_UNITS:
        return qty * MASS_UNITS[unit_std], None

    if unit_std in VOLUME_UNITS_ML:
        ml = qty * VOLUME_UNITS_ML[unit_std]
        key = food_name_std
        if key in grams_per_cup_map:
            g_per_ml = grams_per_cup_map[key] / CUP_ML
            return ml * g_per_ml, None
        return ml * default_density_g_per_ml, "missing_grams_per_cup"

    if unit_std == "count":
        if food_name_std in grams_per_count_map:
            return qty * grams_per_count_map[food_name_std], None
        return None, "missing_grams_per_count"

    return None, "unrecognized_unit"
