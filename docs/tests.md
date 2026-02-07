# Tests and Guardrails

## What is covered
- Parsing and units: `tests/test_parsing.py` mirrors notebook parsing harness expectations (fractions, mixed numbers, quantity-unit-food parsing, grams conversion fallbacks).
- USDA retry behavior and defaults are indirectly exercised via notebooks; keep `usda_client` changes in mind when updating cases.

## How to run
- From repo root (uses .venv):
  - `pytest -q tests`
- From the validation notebook (preferred for business context):
  - Run `notebook/05_validation_tests.ipynb` to execute unit checks plus integration checks against `data/outputs`.

## Common human-facing failures and quick fixes
- Missing USDA key: `ValueError` about `USDA_food_data` or 401s from the API. Fix: add the key to `config/api_keys.json` and rerun.
- Grams are zero or NaN for many items: likely missing qty/unit or unrecognized unit. Fix: adjust `config/defaults_food_items.yaml` or add a grams override, then rerun 99.
- cbg_prev_same_day missing in model_table: lags failed because datetime parsing was off. Fix: rerun 01 to inspect datetime parsing, correct date/time formats or defaults, then rerun 99 and 05.

## When to add tests
- New parsing patterns (e.g., slashes, mixed units) → add a case in `tests/test_parsing.py`.
- Changes to defaults or grams overrides → add an assertion that the expected grams/units appear.
- USDA matching tweaks → add a parsing example to the validation harness (see `validate.validate_parsing_examples`) so regressions are visible.
