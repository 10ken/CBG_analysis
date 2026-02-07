# Copilot instructions for CBG_analysis

## Project overview
- Purpose: basic exploratory analysis for meal and blood glucose logs (see README.md).
- Primary data sources are Excel files under data/ (example: data/20251218_Trudy_Meals.xlsx).
- Working notebook is notebook/data_transformation.ipynb (currently empty).

## Data and domain conventions
- Excel workbook contains multiple sheets with different schemas.
  - "Dec25 Jan26" sheet columns: Date, Time, Meal, BG 2 HRS POST, Protein, Carb, Veggies, Fat, Other.
  - "Feb2026" sheet columns: DATE, TIME, MEAL, BG 2 HRS POST, ITEMS, NOTES.
- Several columns are sparse (Fat, Other, NOTES).
- Date/Time formats are mixed across sheets and need normalization.

## Configuration
- API keys live in config/api_keys.json; treat as secrets and avoid hardcoding keys in code.

## Repo layout
- README.md: data descriptions and high-level notes.
- data/: raw input files (do not overwrite unless explicitly requested).
- notebook/: analysis and transformation work.
- .venv/: local Python environment (not a source of truth).
