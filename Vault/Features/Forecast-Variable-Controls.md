# Forecast variable controls and Meta diagnostics

Added 2026-09-18. Implemented in `backend/forecast_diagnostics.py`, the read-only
`GET /api/forecast/diagnostics` endpoint, and `frontend/src/ForecastDiagnostics.tsx`.
This panel originally shipped on **Forecast**, below the daily spend charts. On
2026-09-18 it moved to **Dataset**, directly under the campaign/ad-set/date scope
bar. Later the same day, the visible variable-toggle block and duplicate
multivariate table were removed: the current `ForecastDiagnostics` UI renders the
declared/expanded correlation matrix only. Forecast no longer renders the
correlation matrix, variable toggles, or multivariate regression table; its
spend-only fit/scatter behavior is unchanged. Dataset's top regression table is
the shared `OlsResultCards` univariate + multivariate view.

## Outcome and source definitions

**Leads (CRM outcome)** is always `daily_ad_set_aggregates.lead_count`. The two
advertising-export counters are stored separately: **Leads (Meta export)** comes
from the source `Leads` column (`daily_ad_performance.leads`), while **Meta Leads**
comes from the explicit `Meta leads` column (`daily_ad_performance.meta_leads`).
Neither is substituted for the CRM outcome or for the other source field. **Cost
Per Lead** uses the export's `Leads` denominator and never CRM Leads.

All values align to daily observations in the selected campaign/ad-set and
inclusive date window. Dataset owns those controls now: the campaign selector,
ad-set lookup, and date-window picker feed both the raw-row browser and this
diagnostic request. Ad set takes precedence over campaign. Comma-separated
campaign IDs work as a union, including the legacy feature-loader spend context
(previously that context incorrectly compared the entire comma-separated string).
Date slicing happens after recency/age features are constructed, so changing the
window does not restart clocks. The window is intersected with the CRM scope's
observed span; future-only windows do not manufacture observations.

| Label | Imported source / daily calculation |
| --- | --- |
| Spend | `Amount spent (USD)` → `amount_spent_usd`; sum |
| Messaging conversations started | Same-named source → `messaging_conversations_started`; sum |
| Cost per messaging conversation started | Daily spend / daily conversations |
| Reach | `Reach` → `reach`; only a single ad-set-day source row (distinct people are not additive) |
| Frequency | Daily impressions / valid daily reach; existing declared predictor, not duplicated |
| Impressions | `Impressions` → `impressions`; sum |
| CTR (all) | Daily `Clicks (all)` / impressions × 100; never substitutes link clicks |
| CPM | Daily spend / impressions × 1,000 |
| Link clicks | `Link clicks` → `link_clicks`; sum |
| Clicks (all) | `Clicks (all)` → `clicks_all`; sum |
| Leads (Meta export) | `Leads` → `daily_ad_performance.leads`; sum |
| Cost Per Lead | `Cost per lead`, recomputed as daily spend / daily export Leads after rollup |
| Meta Leads | `Meta leads` → `daily_ad_performance.meta_leads`; sum and kept separate from CRM Leads |

New nullable `clicks_all`, `ctr_all`, `cpm`, and `meta_leads` columns are migrated at startup.
Their canonical upload headers are `Clicks (all)`, `CTR (all)`, and
`CPM (cost per 1,000 impressions)`. Rates are recomputed from totals. When totals
are missing, a valid **single source row's** reported frequency/CTR/CPM/cost rate
can be retained; rates are never averaged across rows and a known zero
denominator remains undefined. Rounded rates are not inverted to invent counts.

Missing, nonnumeric, infinite and negative metric values remain unknown. A
partially missing daily counter produces an unknown total, not an undercount.
New per-ad imports also retain this strict missing-counter behavior during
rollup; the additive-sum test now uses a reported `0` rather than a blank. Reach
across multiple ads/ad sets cannot be deduplicated and stays unavailable. Older
imports remain readable; fields not retained by older importers need re-upload
from the original export. No historical values are fabricated or backfilled.

The legacy Forecast page had an 860px minimum width. The diagnostics stylesheet
removes that floor at small breakpoints and constrains the parent grid so the
new controls fit on phones while tables scroll within their own containers.

## Selection rules

- All seven declared groups and the requested Meta variables are enabled by default.
- A declared baseline uses the existing grouped forward/backward selector and
  its adjusted-R²/partial-F criteria. The result is memoized by daily data and
  scope/window (not settings); toggles filter that baseline without searching
  the old declared pool again. Data/window changes recompute the baseline.
- Disabling a group removes its selected terms. Re-enabling restores its
  baseline eligibility; a previously rejected declared group is not forced in.
- Only enabled new Meta metrics are forward candidates. Each round selects
  the largest adjusted-R² gain above `1e-6`, with partial F-test `p < 0.10`.
- Candidate/base comparisons use exactly the same complete daily rows. At
  least `max(12, predictors + 6)` rows are required. Accepted candidates may
  reduce the model sample; sample counts are displayed. Rejected candidates
  do not alter accepted predictors or their sample.
- Candidate VIF must be ≤10. A candidate is also rejected if it worsens an
  accepted predictor's VIF above 10. Rank-dependent terms cannot enter.
- Steps report round/order, paired adjusted R² before/after, F-test p, candidate
  VIF, sample size and accepted/rejected/deferred reason. Deferred contenders
  are reconsidered against the winner in the next round.
- The two imported lead counters and export Cost Per Lead are not formulaic CRM
  target leakage, so they may be evaluated. All same-day Meta measurements are **diagnostic only**:
  they are not known future inputs, causal claims, or production forecast changes.

The predictive training and budget-scenario paths are untouched. Do not wire
these user-selected contemporaneous predictors into future forecasts without
a separately validated feature-availability and backtesting design.

## UI and API

`enabled` is a comma-separated predictor allowlist. Omission = all eligible model variables;
an empty string = no predictors. Unknown keys, CRM Leads, and the CRM cost ratio
are rejected with HTTP 400. Dates are validated; reversed dates return 400.
The response combines scope, catalog/status, declared and expanded matrices,
multivariate summary, empty-model reason, and selection trace atomically.

Preferences use local storage `leadlens.forecast.predictors.v1`; they survive
navigation/reload. Clear optional variables keeps declared choices. Restore
defaults enables declared groups and disables Meta metrics. Select all enables
all eligible predictors, not the fixed outcome or descriptive-only ratio.

Accessible switches have stable names. Each variable shows status and data
warnings; status is not color-only. Controls can collapse. New requests hide
stale fit results; abort + generation checks reject out-of-order responses.
Failures show an explicit retry control.

The expanded matrix includes every encoded term, the Meta metrics, and the
descriptive CRM ratio, including disabled/constant/missing variables. Excluded
rows remain muted and labeled. Pairwise Pearson correlations require ≥3 pairs
and variation in both columns; N/A cells include explanations and pair counts.
The Model view uses the strongest signed encoded-term pair per group,
including its identity in the tooltip; undefined diagonals stay N/A.
Tables scroll horizontally and retain sticky row labels. UI/UX Pro Max guidance
informed readable controls, focus/loading states and responsive grouping;
the existing fonts, spacing and dual-theme/correlation tokens remain in use.

## Verification

`tests/test_forecast_diagnostics.py` covers source separation, strict daily
aggregation, imports/schema, missing/constant data, accepted/rejected candidates,
collinearity, baseline caching and toggles, leakage rejection, empty models,
inclusive campaign/ad-set/date scoping, and JSON-safe responses.
`frontend/tests/forecastVariableSettings.test.mjs` covers preference persistence,
toggle/bulk behavior, fixed-outcome protection and scoped API queries.
Run `.venv/Scripts/python.exe -m pytest -q` and `npm test` / `npm run build`
inside `frontend`. Browser checks use a local copy of the database, not a live
deployment. Missing source data is visible in the UI; no fake demo data is used.

Verified: 280 backend tests and 5 frontend preference/query tests pass; production
build succeeds (existing large-bundle warning only). Browser checks confirm
Spend exclusion changes coefficients, an enabled conversations metric can be
accepted, Meta leads remains a distinct unavailable column when absent, and
June 15–30 produces 16 observations. Light/dark and 375px responsive layouts
were inspected; no browser console errors were reported during these checks.

## Railway release — 2026-09-18

Deployed successfully to `Explorer-by-SL-Leads` / `production` via a source-only
Railway CLI upload, deployment `2a3571b9-6a16-4841-96c5-15223146b334`.
The release preserves the workspace's existing Follow-up/service-selector changes;
no local Git changes were discarded or committed. The upload was staged from the
Dockerfile's runtime/build allowlist and excluded databases, uploads and secrets.
Railway reports SUCCESS; `/api/health` returns 200, unauthenticated diagnostics
returns 401, and the live `index-UKIKhyh8.js` bundle contains the new controls and
separate Meta leads labeling. The existing `/data` volume remains mounted.

## Railway release — 2026-09-18, Dataset move

Moved the diagnostics panel from Forecast to Dataset and deployed via Railway CLI
upload, deployment `9c6e0953-18ee-42a8-aec4-96b3c7f46cfb`. Forecast now keeps only
the chart-level spend diagnostics; Dataset owns the diagnostics area under its
campaign/ad-set/date scope bar. Railway reports SUCCESS; `/api/health` returns
200, unauthenticated diagnostics returns 401, and the live `index-i8Ac3p6Y.js`
bundle contains the Dataset diagnostics controls and separate Meta leads
labeling. The existing `/data` volume remains mounted.

## Railway release — 2026-09-18, OLS-first Dataset layout

Deployed the Dataset diagnostics layout revision via Railway CLI upload,
deployment `23697ab7-36c0-4514-a24b-ada88e0ce37f`. The top "Model diagnostics" /
"Variables to consider" block and the duplicate diagnostics multivariate table
are removed from the rendered Dataset page. The shared univariate + multivariate
`OlsResultCards` table now sits directly under the Dataset scope bar, before the
correlation matrix. Railway reports SUCCESS; `/api/health` returns 200,
unauthenticated diagnostics and OLS APIs return 401, and live `index-tLfB4yK4.js`
contains `Spend-only OLS`, `Multivariate OLS`, and `Correlation matrix` while no
longer containing `What explains Leads?` or `Variables to consider`.

## Requested Meta variables and demographic import — 2026-09-18

The Dataset multivariate diagnostic selection pool now includes the requested
same-day variables: Messaging Conversation Started, Cost Per Messaging
Conversation started, Reach, Frequency, Impressions, CTR (all), CPM, Link Clicks,
Clicks (all), Leads (Meta export), Cost Per Lead, and Meta Leads. Forward selection
still determines which estimable terms appear in the multivariate OLS coefficient
table. These fields do not enter the production forecast.

The ad-performance importer now recognizes the explicit `Meta leads` header and
persists it independently. It also recognizes Age/Gender segmented Meta exports.
Those mutually exclusive demographic rows are rolled up to ad-set-day grain before
deduplication: additive counters sum, Reach sums across demographic partitions,
Frequency/CTR/CPM/cost rates are recomputed from totals, and blank segment counters
represent zero for that segment. A fixture shaped like the supplied 17,034-row CSV
and a full-file dry run verify that all additive totals reconcile after rollup when
campaign/ad-set attribution is available.

## Mixed legacy/new rows no longer blank the matrix — 2026-09-18

The first release required every in-scope ad-set row on a day to contain a metric
before producing that day's total. That made portfolio correlations report `N/A`
after a valid new-variable upload whenever any older ad-set row still had NULL in
the newer columns. Reach/frequency had an additional single-row restriction, so
they were always unavailable outside one ad set.

`aggregate_meta_daily()` now sums the available non-negative observations for each
day instead of letting one legacy NULL veto the whole day. Reach is summed across
the available ad-set rows for diagnostics, with the catalog stating that portfolio
audiences may overlap; Frequency is recomputed from daily impressions / summed
reach. CTR, CPM, and both cost ratios remain ratios of daily totals. A day where
every row lacks a field stays missing. Demographic import rollups now write an
explicit zero when every segment's additive counter is blank, matching Meta's
blank-as-zero export convention. A mixed legacy/new-row regression test verifies
that every requested variable has an estimable correlation diagonal.

## Expanded matrix holiday proximity — 2026-09-18

The expanded diagnostics matrix now includes a grouped `holiday_proximity` column
in addition to the individual encoded holiday buckets. The grouped column is an
ordinal proximity score derived from the bucket dummies so users can read a
single Holiday proximity relationship in Expanded view even when one bucket such
as `holiday_0_14_days` is unavailable or constant for the current scope.

## All-variable multivariate OLS — 2026-09-23

The shared Multivariate OLS card on **Dataset** and **Forecast** now fits every requested
predictor that is available and independently estimable in the current campaign/ad-set
scope. The requested set is the seven original predictor groups plus all eleven additional
Meta groups (Frequency is shared and therefore appears only once). Forward selection no
longer removes otherwise usable variables from this displayed accounting regression.

Constant, entirely unavailable, and rank-redundant encoded columns are still omitted because
OLS cannot estimate them; the response exposes a status and reason for every requested group,
and the card reports how many were included. Day of week and holiday proximity remain grouped
variables even though they expand into multiple coefficient rows. The card explicitly labels
the dependent variable as CRM Leads and keeps **Meta Leads** as its own predictor.

This changes the diagnostic table only. The production forecast path still uses its separately
backtested, future-available feature set; contemporaneous Meta outcomes are not silently treated
as known future values.

Deployed to Railway production on 2026-09-23 via source upload, deployment
`3fc0ef31-e6fc-4d35-8112-407a6a3ad718`. Railway reported `SUCCESS`; the public health endpoint
returned 200, the unauthenticated OLS endpoint returned 401, and the live frontend bundle
contains the all-variable regression copy distinguishing CRM Leads from Meta Leads.
