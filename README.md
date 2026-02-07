# CBG_analysis

Basic exploratory analysis for meal and blood glucose logs.

## Data

Input files live in the data folder.

- data/20251218_Trudy_Meals.xlsx
	- Sheets: Dec25 Jan26, Feb2026
	- Dec25 Jan26: 192 rows x 9 columns
		- Date, Time, Meal, BG 2 HRS POST, Protein, Carb, Veggies, Fat, Other
	- Feb2026: 14 rows x 6 columns
		- DATE, TIME, MEAL, BG 2 HRS POST, ITEMS, NOTES

## Notes

- Several columns are sparse, especially Fat, Other, and NOTES.
- Date/Time fields use mixed formats across sheets and will need normalization.