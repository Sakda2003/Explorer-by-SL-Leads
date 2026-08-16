# Forward Selection Notebook

> [!note] This note is about the two standalone notebooks. The app runs its own selector —
> see [[Forward-Selection-In-The-App]] for the one that picks the Multivariate OLS card's
> variables, which uses a stricter entry rule (adjusted R² gain **and** block F p < 0.10) than
> either notebook here.

> [!note] There are now two forward-selection notebooks — 2026-08-13
> `forward_selection_all_variables.ipynb` (below, second section) was written from the same
> BA222 LN08 source but supersedes `forward_selection.ipynb` on every axis: it reads the
> curated `master_dataset.xlsx` instead of four separate workbooks, treats categoricals as
> whole variables via `smf.ols` + `C()` instead of one candidate per dummy, verifies its
> exclusion rules against the data with asserts, reports a rejection margin for every
> variable left out, and adds backward selection for comparison. **Decide whether to delete
> the older one** — keeping both invites two answers to one question.

## `forward_selection_all_variables.ipynb` (2026-08-13)

Built at the user's request after the app-side forward-selection work was paused. 22 cells,
runs end to end against `Dataset/master_dataset.xlsx` (`master_adset_daily`, 879 rows,
18 ad sets, 2026-06-06 → 2026-07-31).

**Three things that were not obvious:**
- **LN08's own loop (Section 4) has a bug and this notebook does not copy it.** The lecture
  code seeds `maxR2` with the first variable's `.rsquared`, then compares later candidates'
  `.rsquared_adj` against it. Adjusted R² is always the smaller quantity, so the second
  variable is judged against an inflated bar and the search can stop one step early. The
  notebook recomputes the seed model's adjusted R² before the loop and says so in prose.
- **Categoricals enter as blocks, not as dummies.** `C(day_of_week)` is one candidate. This
  matches LN08 Section 3.2 (it refuses to drop `C(buildingStyle)` over one insignificant
  level) and matches the whole-variable convention chosen for the app-side work. Backward
  selection therefore tests blocks with `anova_lm(typ=2)`, not per-level t-tests.
- **Rows are dropped for missing values once, up front, not per model.** statsmodels drops
  them per fit, which would silently change `n` between two models whose adjusted R² are
  being compared. One row (zero impressions → undefined `cpm`) goes.

**Result on this data:** `messaging_conversations` enters first at adjusted R² **0.9013**
and the remaining seven selected variables add **0.003 in total** — impressions,
ad_change_type, ad_set_budget, reach, days_since_adset_started, frequency, spend, in that
order, final adjusted R² 0.9040. With the notebook's `min_gain=0.001` materiality floor the
model collapses to two variables (0.9025). Backward selection keeps four
(messaging_conversations, impressions, reach, ad_set_budget) at 0.9031. All three readings
say the same thing: **leads are messenger conversations, and nearly everything else is
decoration.** Same conclusion the older notebook reached on the same underlying window.

`spend` is selected but last, worth +0.00002, p = 0.28 — it is not that spend does not
drive leads, it is that conversations already carry spend's effect.

**Why:** the user asked for a reusable tool to run forward selection over *every* variable,
inspired by the lecture notes directly.
**How to apply:** the interesting knobs are `CANDIDATE_SET = "declared"` (restrict to the
eight declared drivers, which reproduces the app's framework for comparison) and
`CAMPAIGN_FIXED_EFFECTS`. Re-run after `python build_master_dataset.py` — the greedy path
can change with more data, and this file is stale relative to the live database (which is at
30 ad sets through 2026-08-12). Note the workbook's change-recency/type columns are the
**inferred** detector outputs the app abandoned 2026-08-06 as wrong; treat any finding about
them as provisional. See [[Master-Dataset]] and [[OLS-Declared-Ten-Variables]].

## `forward_selection.ipynb` (2026-08-08, superseded)

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
