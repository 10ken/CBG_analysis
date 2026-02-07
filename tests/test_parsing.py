import math
import sys
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.io_excel as io_excel
import src.parse_foods as parse_foods
import src.units as units

importlib.reload(io_excel)
importlib.reload(units)
importlib.reload(parse_foods)

_parse_bg_value = getattr(io_excel, "_parse_bg_value")
_expand_compound_item = getattr(parse_foods, "_expand_compound_item")
parse_quantity_unit = getattr(units, "parse_quantity_unit")


def _report(case: str):
    print(f"[TEST] {case} ... OK")


def test_parse_bg_values():
    print("[BG] Parsing BG values with single and dual readings")
    assert math.isclose(_parse_bg_value("6.4 (CGM 5.8)"), 6.1, rel_tol=1e-6)
    _report("BG dual reading avg: 6.4 and 5.8 -> 6.1")
    assert math.isclose(_parse_bg_value("CGM 4.9"), 4.9, rel_tol=1e-6)
    _report("BG single reading: 4.9 -> 4.9")
    assert math.isnan(_parse_bg_value(""))
    _report("BG empty string -> NaN")


def test_compact_number_word_parsing():
    print("[UNITS] Parsing compact number+word items (count units)")
    pq = parse_quantity_unit("2eggs")
    assert pq.qty_numeric == 2
    assert pq.unit_std is None
    assert pq.food_name_std == "eggs"
    _report("2eggs -> qty=2, unit missing, food=eggs")


def test_compound_two_foods_even_split():
    print("[COMPOUND] Evenly split qty across two foods")
    items = _expand_compound_item("1 cup beef brisket/tendon")
    assert len(items) == 2
    assert "0.5" in items[0] and "0.5" in items[1]
    assert "beef brisket" in items[0]
    assert "beef tendon" in items[1]
    _report("1 cup beef brisket/tendon -> two items at 0.5 cup each")


def test_skyr_blueberries_flax_hemp_kiwi_expansion():
    print("[COMPOUND] Special skyr/berries/seeds/kiwi expansion with defaults")
    items = _expand_compound_item("1/4 cup skyr/blueberries/flax/hemp/kiwi")
    assert len(items) == 5
    assert any("skyr" in i for i in items)
    assert any(i.startswith("100 g blueberries") for i in items)
    assert any(i.startswith("3 tsp flax seed") for i in items)
    assert any(i.startswith("3 tsp hempseed") for i in items)
    assert any(i.startswith("1 count kiwi") for i in items)
    _report("1/4 cup skyr/blueberries/flax/hemp/kiwi expanded to 5 items with defaults")


def test_quarter_yogurt_blueberries():
    print("[COMPOUND] Quarter cup yogurt plus default blueberries")
    items = _expand_compound_item("1/4 yogurt/blueberries")
    assert "0.25 cup yogurt" in items
    assert "100 g blueberries" in items
    _report("1/4 yogurt/blueberries -> yogurt 0.25 cup, blueberries 100 g")


def test_skyr_blueberries_flax_hemp_chia_apple_expansion():
    print("[COMPOUND] Skyr + berries + seeds + apple expansion with defaults")
    items = _expand_compound_item("1/4 skyr blueberries/flax/hemp/chia/1/4 green apple")
    assert "0.24 cup skyr yogurt" in items
    assert "100 g blueberries" in items
    assert "3 tsp flax seed" in items
    assert "3 tsp hempseed" in items
    assert "3 tsp chia seeds" in items
    assert any("apple" in i for i in items)
    _report("Skyr + berries + seeds + apple expanded with defaults")


if __name__ == "__main__":
    print("[RUN] Starting parsing tests (script mode)")
    test_parse_bg_values()
    test_compact_number_word_parsing()
    test_compound_two_foods_even_split()
    test_skyr_blueberries_flax_hemp_kiwi_expansion()
    print("[RUN] All parsing tests passed.")
