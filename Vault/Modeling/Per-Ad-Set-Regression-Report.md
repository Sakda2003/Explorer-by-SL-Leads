# Per-Ad-Set Regression Report

Built 2026-08-13. `build_regression_report.py` at the project root renders one standalone HTML
page covering every ad set in `daily_ad_set_aggregates`: a ranking table, then a section per ad
set carrying a univariate table, a multivariate table, a correlation matrix, and the declared
variable coverage list.

```
python build_regression_report.py [output.html]   # default Dataset/regression_report.html
```

Published as a private artifact: https://claude.ai/code/artifact/8ab55984-b534-4fd6-a8ba-4b1fb9554935
(republish the same file path from the conversation that created it to keep that URL).

## Current regression-table display

Per request 2026-08-13, the report's univariate table is spend-only: `leads ~ spent`.
The ranking board now shows spend-only R2, and each ad-set section either shows the one
`spent` coefficient row or an empty state when spend has no usable variation in that ad set
window.

The multivariate table uses the compact coefficient style from the app's OLS output. Every ad
set gets exactly eight rows: Intercept, Spent, Holiday_proximity,
days_since_ad_set_started, frequency, ad_change_recency, ad_set_change_recency, and
Days_of_the_week. The columns are coefficient, standard error, t, p-value, and the 95%
confidence interval.

The underlying multivariate model uses every varying encoded term that adds independent
information. Holiday_proximity and Days_of_the_week have multiple encoded columns, so the
display row uses the strongest fitted encoded term by absolute t-statistic for the coefficient
values, but does not print the underlying term name. Constant, missing, not-fitted, and
rank-pruned groups stay visible with dashes.

Also per request 2026-08-13, the declared-variable coverage cards suppress the underlying
holiday-bucket and weekday lists. The report stays at the grouped eight-variable level in the
coefficient table and the coverage panel.

Also fixed 2026-08-13: `_select_multivariate_ols_features` now drops any varying feature that
does not increase the design matrix rank after the intercept and earlier declared terms are in
place. This removes fake standalone coefficients for perfect linear dependencies. Example:
on `120238338920760078`, `days_since_ad_set_started` was always `ad_change_recency + 137`.
With an intercept, those two columns are the same signal, so `ad_change_recency` is omitted
from that ad set's multivariate fit and marked `omitted` in the table. The same rank-prune
also keeps only independent weekday indicators instead of fitting all seven with an intercept.

## Superseded univariate note

The app's **Univariate OLS** card is `leads ~ spend` and nothing else
([[OLS-Declared-Ten-Variables]]). This report's univariate table is **one separate simple
regression per declared predictor** — spend, each holiday bucket, ad set age, frequency, both
change-recency variables, all seven weekdays — each with its own slope, standard error, t, p,
Pearson r, and R². Both readings are legitimate; the report picks the per-variable one because
it sits next to the correlation matrix and answers the same question in coefficient form. Don't
compare the report's "best univariate R²" column against the app's Univariate card and expect a
match unless spend happens to be the top predictor.

No reimplementation: the script calls `core._load_scope_feature_rows`, `core._fit_ols_summary`,
`core._select_multivariate_ols_features`, `core.get_dataset_correlation`, and
`core._declared_variable_coverage` at ad-set scope. If those change, the report changes with
them, which is the point.

## What the run on 2026-08-13 showed

After the August 12 refresh: 30 ad sets, 22 with a multivariate fit, median adjusted R²
0.169. `120245126806080078` (VISA | EU | FOR) leads at 0.526, followed by
`120238338920760078` (VISA | CA | KHM) at 0.524. Spend clears p < 0.05 on its own in 13 of
the 18 ad sets where spend moves.

Eight ad sets carry too few days to fit. Five of them have a one-day window, where every
candidate predictor is constant. They still get a section, marked "Insufficient data" with the
term count the model wanted against the days available, rather than being dropped.

**`days_since_ad_set_started` and `ad_change_recency` correlate at exactly r = 1.00 on `120238338920760078`.**
Both count days from a single recorded date, and for that ad set the
recorded change date and the recorded start date coincide, so the two columns are the same
series. Their individual coefficients in the multivariate table split one shared signal and
cannot be read apart. The report detects any predictor pair at |r| ≥ 0.95 and prints that
warning under the matrix, so this surfaces on its own if it appears elsewhere later.

## Three variables are labelled by feature key, not by core.py's friendly label

Per request 2026-08-13, the report prints `days_since_ad_set_started`, `ad_change_recency`, and
`ad_set_change_recency` where `core._feature_label` would say "ad set age", "ad change recency",
and "ad set change recency". The script patches `core._feature_label` at import time rather than
rewriting strings after the fact, because a label reaches the page from four different places —
the univariate rows the script builds, the coefficient terms `_fit_ols_summary` builds, the
matrix axis labels `get_dataset_correlation` builds, and the coverage details
`_declared_variable_coverage` builds. Patching the one function catches all four; string
replacement afterwards would have missed at least the matrix.

**The app's own UI is untouched** — the Dataset and Forecast pages still say "ad set age". To
propagate the change app-wide instead, edit the `labels` dict in `_feature_label`
(`backend/core.py`, around line 4286) and drop `REPORT_LABELS` from this script.

Two knock-on details: variable 4's declared spec name is `days_since_adset_started` (no
underscore between "ad" and "set"), so `COVERAGE_NAMES` respells it to match the tables; and
three coverage cards now carry the same string as both name and detail, so the detail is
suppressed when it equals the name.

## Build notes worth keeping

- **Fonts are embedded as base64 data URIs.** The artifact CSP blocks font CDNs, so a
  `@font-face` URL would silently fall back. The script reads Inter Tight and JetBrains Mono
  straight out of `frontend/node_modules/@fontsource-variable/*/files/*-latin-wght-normal.woff2`
  (45 KB + 40 KB), which is why the frontend's dependencies must be installed to build the
  report. Colours come from the shipped tokens in `frontend/src/styles.css`, including
  `--corr-cold` / `--corr-zero` / `--corr-hot` for the matrices — see
  [[Dual-Theme-Redesign]] and the design-system MASTER.
- **Two layout traps, both fixed:** grid items default to `min-width: auto`, so the wide
  regression tables pushed the whole page sideways until `.pair > .panel { min-width: 0 }` went
  in; and one legacy campaign name carries a long creative-code tail
  (`Leads|Visa|Australia|Cambodian - #V0010M1, ...`) which, under the table's `white-space:
  nowrap`, stretched the ranking table to 1232 px inside an 1130 px container and clipped the
  adjusted-R² column. The ad set name cell now wraps at a 300 px cap.
- Verified in forced light mode with Playwright + Edge, including a simulated dark-system
  preference; the root theme lock keeps the light tokens active.

**How to apply:** regenerate after any retrain or data import; the script reads the live
database and takes about a minute. Nothing in it writes to the database.

## Forced light theme and August 12 refresh

Refreshed 2026-08-13 after the August 8-12 ad-performance and customer-traffic imports.
Training run 313 completed on 3,674 CRM leads across 30 ad sets before the HTML and PDF were
rebuilt. The report window now ends 2026-08-12.

The generated document now sets `data-theme="light"` on its root element. This prevents a
dark operating-system preference from activating the report's legacy dark-media block, so the
interactive report and PDF stay light on every machine. PDF export still prints background
colours to preserve the correlation scale and score bars.

## Compact declared-variable correlation matrix

Remodeled 2026-08-13 to match the Dataset-page reference. The report now collapses the
feature-level matrix into the eight declared groups: Leads, Spend, Holiday proximity, Ad set
age, Frequency, Ad change recency, Ad set change recency, and Day of week. Constant groups are
omitted, so a narrow ad-set window can show fewer than eight rows and columns.

For each off-diagonal group pair, the display selects the underlying feature pair with the
largest absolute Pearson correlation and retains its sign. This exactly matches the compact
matrix logic in `frontend/src/App.tsx`; the diagonal is fixed at 1.00. It is a display collapse,
not the correlation of a synthetic grouped variable or an average of the underlying values.
Holiday proximity and Day of week can therefore be driven by one indicator. Collinearity
warnings continue to inspect the full expanded feature matrix.

The visual is a connected, full-width table with horizontal labels, two-decimal values, and a
coral/steel diverging scale on the forced light theme. Print pagination keeps each matrix and
its explanatory note together.
