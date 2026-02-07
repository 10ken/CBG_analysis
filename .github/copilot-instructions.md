# Copilot instructions for CBG_analysis
You are an MCP Enabled Agent working on the CBG_analysis repository. Your task is to assist with data transformation and analysis related to meal logs and glucose patterns. Please follow the guidelines outlined in this instruction file when providing code suggestions or explanations.

## Project overview
- Purpose: normalize meal logs, attach nutrient context, and relate meals to post-meal glucose (see README.md).
- Primary data sources are Excel files under data/ (example: data/20251218_Trudy_Meals.xlsx).
- Working notebook is notebook/data_transformation.ipynb (currently empty).

## Data and domain conventions
- Excel workbook contains multiple sheets with different schemas.
  - "Dec25 Jan26" sheet columns: Date, Time, Meal, BG 2 HRS POST, Protein, Carb, Veggies, Fat, Other.
  - "Feb2026" sheet columns: DATE, TIME, MEAL, BG 2 HRS POST, ITEMS, NOTES.
- Several columns are sparse (Fat, Other, NOTES).
- Date/Time formats are mixed across sheets and need normalization.
- Business goal: keep outputs usable for glucose pattern analysis; preserve auditability (assumptions/defaults, USDA misses) and surface clear prints/comments.

## Configuration
- API keys live in config/api_keys.json; treat as secrets and avoid hardcoding keys in code.
- Comments/prints: add concise, business-aware messaging (what step, what it means for glucose analysis) rather than generic debug logs.

## Repo layout
- README.md: data descriptions and high-level notes.
- data/: raw input files (do not overwrite unless explicitly requested).
- notebook/: analysis and transformation work.
- .venv/: local Python environment (not a source of truth).
- src/: pipeline code (ingest → parse → defaults → grams → USDA → aggregation → validation). Keep docstrings tied to the meal-to-glucose use case.
