# Forward Selection Notebook

`forward_selection.ipynb` (project root), built 2026-08-08 from the BA222 Lecture Notes
08 (Rhoads) forward-selection method — correlation-seeded first variable, then greedy
add-by-adjusted-R² until no candidate improves it. Runs on the pooled `ad_set_days`
sheet from the four `Full Information Dataset` workbooks (`06-06 to 07-10` through
`07-25 to 08-01`, 879 rows, zero overlapping `(ad_set_id, date)` rows across files).

Target is `leads`. 26 candidates after exclusions: `cpl` and `zero_lead_day` are dropped
as target leakage (both are direct functions of `leads`); `ad_set_budget_type` is
constant in this data; `holiday_proximity` is 1:1 redundant with `is_holiday`; the raw
`ad_set_change_type`/`ad_change_type` columns are 100% NaN in this export (only the
`..._inferred` variants are populated — see [[Master-Dataset]]). Categoricals
(`day_of_week`, `delivery_status`, the two `..._inferred` change-type columns) are
one-hot encoded with an explicit held-out reference level (modal category), matching
this project's collinearity-avoidance convention from [[OLS-Declared-Ten-Variables]] —
each dummy is then a separate candidate, same as the declared-ten panel already does.

**Result on this data:** 11 of 26 candidates selected, final adjusted R² = 0.9064.
`messaging_conversations` enters first (adj R² 0.90 alone) and dominates the fit — leads
are driven almost entirely by messenger-conversation volume in this dataset, with
`impressions`, `ad_set_budget`, `reach`, `frequency`, and several change-type/day-of-week
dummies adding small marginal improvements after it. `spend` itself never improves
adjusted R² once `messaging_conversations` is in the model and is left out.

**Why:** the user asked for a reusable forward-selection tool across all variables, not
a one-off fit — the notebook is meant to be re-run as new `Full Information Dataset`
workbooks are added (just extend `DATASET_FILES`).
**How to apply:** don't read the specific selected-variable list as a permanent
conclusion — re-run whenever the dataset grows, since forward selection's greedy path
can change with more data. `messaging_conversations` swamping `spend`/`cpm` is expected
given the platform mix in this data (see `platform` in `model_dataset`, dominated by
`messenger`).
