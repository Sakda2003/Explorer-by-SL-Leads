# OLS on the Declared Ten Variables

> [!warning] It is eight variables now, not ten — 2026-08-11
> Variables **9 (`ad_set_change_type`) and 10 (`ad_change_type`) were deleted outright**:
> from the model, the OLS panel, the correlation matrix, the variable dictionary, all three
> tables, the recorder UI, and the database. The note title and filename are kept so existing
> `[[OLS-Declared-Ten-Variables]]` links keep resolving. The declared set is now 1–8.
>
> Everything below about change **types** — the inferred detection rules, the held-out
> reference level, the per-unit-of-share coefficient reading — is therefore **history, not
> current behaviour**. The two change **recency** variables (6 and 7) are unaffected and still
> live. See [[Change-History-Hand-Recording]] for what replaced this.

The Multivariate OLS panel models exactly the ten declared variables and nothing else
(rebuilt 2026-08-03). `last7_leads` was removed — it had been riding along as a
"momentum control" but was the strongest regressor, so every declared variable was
being read against a lagged copy of the target. Removing it moved `frequency` from
95.3 (p=0.017) to 2.6 (p=0.83), which is how much of that fit was artifact.

**Three things that were not obvious:**
- **Change types are inferred, never reported.** Detection rules are duplicated
  between `backend/core.py` and `build_master_dataset.py` — change both or they drift.
  See [[Master-Dataset]].
- **The type dummies need a held-out reference level.** Every ad set carries exactly
  one change type on every live day, so the group's shares sum to 1 and are collinear
  with the intercept. Without a reference category, a single lopsided day sets every
  coefficient. A helper detects this and holds out the modal level.
- **Change-type coefficients are per unit of share, not per event.** At portfolio grain
  the dummies are the share of live ad sets in each state — a coefficient of -377 means
  "if the whole portfolio sat in that state," and the realized share tops out around
  0.067, so the actual effect is much smaller than the raw number looks.

**Holiday_Proximity fix (2026-08-03):** the source `holiday_proximity.xlsx` had a
bucketing bug — isolated single-day holidays were flagged `is_holiday=1` but never
bucketed as `during_holiday` (multi-day clusters were fine). Fixed via
`fix_holiday_proximity_bucketing.py`; `master_dataset.xlsx` rebuilt. Panel moved from
R² 0.774 to R² 0.780 (Adj R² flat — the extra term's cost roughly offsets its fit).

If the live server was already running when the CSV was fixed, it needs a restart —
the holiday map is cached and only re-reads the file on process start.

**Where it renders (updated 2026-08-12):** Dataset (portfolio-wide) and the Forecast page
(scoped to the selected campaign or ad set), via one shared component. Note
the share-vs-indicator distinction above applies to the pooled scopes only — at ad-set scope
the change-type dummies are plain 0/1. See [[Forecast-Page-OLS-Panel]].

**Why:** the panel is meant to express the declared causal framework, not maximize R².
**How to apply:** don't re-add helper regressors to make the fit look better, and don't
"fix" a large change-type coefficient without checking the share range first. See
[[Forecast-Flatness-Is-The-Data]] for what this model is actually competing against.

**Variables 4/6/7/9/10's detector fallback removed entirely, 2026-08-06** — not just
hidden from two display tables (see [[Ad-Decision-Engine]]), but deleted from the
functions that feed this OLS fit and the correlation matrix, after the user confirmed
the inferred values were wrong and asked for the fix to reach the actual model, not
just the UI. `_days_since_start_values`, `_ad_change_features`, `_ad_set_change_features`
(`backend/core.py`) now use **confirmed change-log / start-date rows only** — no more
step-shift or per-ad-activity fallback when nothing is recorded. An ad set with nothing
confirmed reports 0 recency / baseline type, which `_load_scope_feature_rows` and
`get_dataset_correlation` both already treat as a constant column and drop automatically
(same "zero variance = undefined correlation" rule that already dropped flat holiday
buckets). Portfolio-wide today: Multivariate OLS R² dropped from 0.780 to 0.501 and the
declared correlation matrix shrank from 10x10 to 5x5 (Leads/Spend/Holiday proximity/
Frequency/Day of week) — an honest number, not a regression to fix. The detector code
itself (`_ad_change_events`, `_ad_set_change_events`, `_lead_ad_spans`, `_creative_code`,
`_template_key`, `_step_change_days`, `_step_ratio`, ~230 lines) was deleted outright, not
commented out — confirmed unused anywhere else in the repo first. If a better detector is
built later, it needs rebuilding from git history, not un-commenting. Recording real data
via the "Ad set change" popover ([[Change-Event-UI-Recorder]]) or a confirmed change-log
upload ([[Change-Log-Importer]]) is the only way these 5 variables contribute again — and
once recorded, they flow through automatically, no code change needed.

**"Recorded but the matrix doesn't change" bug, fixed 2026-08-06 (same day, reported
right after the above shipped).** The backend was never the problem — confirmed data
flowed through `_load_scope_feature_rows` correctly the whole time (verified directly:
`GET /api/dataset/correlation` reflected a saved start date immediately). The bug was
entirely client-side: `DatasetPage`'s correlation/OLS fetch effect (`frontend/src/
App.tsx`) was keyed only on `[scopeParams]` — nothing about `ChangeEventButton` saving
or deleting a change/start date ever touched that dependency array, so the page kept
showing pre-edit numbers until the scope filter happened to change too (or a full
reload). Same latent bug existed in `ForecastPage`'s OLS effect, unrelated to the
2026-08-06 extraction — it predates it. Fixed by giving `ChangeEventButton` an optional
`onChange` prop, called after every successful save/delete, wired to a `dataRefreshKey` /
`modelRefreshKey` counter in each parent page's fetch-effect dependency array. See
[[Change-Event-UI-Recorder]].

**Recording a change now retrains automatically, 2026-08-06.** Previously the five
variables reached the live-computed surfaces (correlation matrix, OLS panel, and — after
the same-day fix in [[Dataset-Page]] — the raw row table) the instant a change was saved,
but every *stored* forecast stayed on the last manual training run until a manual retrain.
`save_change_event` / `save_ad_set_start_date` and
both delete routes now schedule `train_models()` as a FastAPI `BackgroundTasks` job
(`_request_retrain` in `backend/app.py`), with `GET /api/models/retrain-status` for the
UI to poll. Measured at ~18s portfolio-wide, so running it inline would have hung the
popover — this had to be background, not a synchronous call.

The guard is **single-flight with a one-slot queue**, not "skip if busy": a run already
in flight may have read the aggregates before this save landed, so its output wouldn't
reflect the edit. One follow-up pass is queued, and further saves during that window
collapse into the same pass rather than stacking N full retrains. `_retrain_running` is
set inside the request handler (not in the background task), so it is already true by
the time the client sees the response — the frontend's `useRetrainWatcher` poll can't
race the task's own start. Failures record to `last_error` and still release the flag,
so a failed retrain can't wedge the guard permanently.

Frontend: `useRetrainWatcher(onComplete?)` (`App.tsx`) polls every 2s while armed. The
Dataset page uses it for the "Retraining forecasts with this change…" indicator only —
everything it renders is computed live, so it needs no second refetch. The Forecast page
*does* pass an `onComplete` (re-runs `load()` and bumps `modelRefreshKey`), because it
renders stored forecasts; its first refresh on save only corrects the live OLS fit.

**Caution for future testing:** the calendar in `SingleDatePicker` defaults to today's
real system date when no value is set, not the app's Aug 2026 dataset window — clicking
the "first available cell" during a quick check can silently record a not-obviously-wrong
but out-of-window date (a August 2026 date against a Jun6-Jul31 observed range has zero
variance and gets dropped from the matrix, which looks like "nothing happened" and can
be mistaken for a bug). Also: verify a database row isn't the user's own real data (check
`created_at`, or read the actual recorded dates for a real-looking pattern) before
deleting it as "test cleanup" — one such deletion happened while chasing this exact bug
and had to be restored from a still-visible API response.

**Superseded same day — change types became point events (2026-08-07).** The analysis in
the next paragraph diagnosed the symptom correctly, but the real fix was to stop carrying
the type forward at all: the indicator now fires only on the day of the change, and every
other day is the all-zero "no change" reference. That makes the column vary by construction
(1 event day among 55 no-change days), so neither the "record it later in the window" nor
the "record a second event" workaround is needed anymore. Both #9 and #10 now appear in the
matrix for the user's real ad set. Kept below because the collinearity reasoning still
explains why a *constant* dummy group is dropped, which is still live behaviour for any
genuinely constant column. Full redesign: [[Change-Event-UI-Recorder]].

**A change type dummy needs a baseline day before it, not just an in-window date,
2026-08-07.** Distinct from the trap below (out-of-window dates): the user re-recorded
`120236217374900078`'s events correctly *inside* the window this time (both start
2026-06-06, the ad set's own first observed day) and `ad_set_change_type` /
`ad_change_type` still didn't appear in the matrix, while `ad_set_change_recency` /
`ad_change_recency` did. Root cause, confirmed with a scratch A/B test (`get_dataset_
correlation` before/after two save_change_event calls on a throwaway ad set): recency
is a day-count that varies from 0 upward regardless of where the event falls, but the
*type* dummy is 1 for every day from the event's start date onward — if that start date
is on or before the scope's earliest observed day, there is no earlier "baseline" day
left in the data where the type reads 0, so the column is constant for the entire
window and correctly drops as zero-variance (not a bug — a real undefined correlation).
Case A (event == first day): matrix has no type column. Case B (event mid-window):
type column appears immediately. Recording a *second*, differently-typed event would
also work (varies the type without needing a pre-event baseline day), and is the only
option when the true first change genuinely happened on day one of the data.

`_declared_variable_coverage()` (`core.py`) already computed this correctly as
`status: "flat"` — the "Variable dictionary" section at the top of the Dataset page
was already right — but the correlation section below it gave no indication anything
was missing, so a statistically-correct silent drop read as a bug. Fixed two ways,
same day: (1) the flat-status branch now distinguishes "never confirmed" (every
feature's mean is 0) from "confirmed but covers the whole window" (some feature's mean
is a constant >0) for variables 9/10 specifically — recency (6/7) reads the same "0
forever" for "nothing recorded" with no analogous fix, so it keeps the generic message;
(2) the Dataset page now cross-references `declaredCorrelation.variables` against
`ols.declared_variables` and renders a `.dataset-correlation-missing` box right under
the matrix listing exactly which of the ten are absent and why — reusing the existing
`_declared_variable_coverage` computation rather than re-deriving flatness client-side,
so the note and the matrix can never disagree. Verified live against the user's actual
recorded data: note read "#9 Ad set change type / #10 Ad change type — Recorded, but
covers the entire window -- no earlier baseline day".

**An in-window event date is a hard requirement, and the calendar makes it easy to miss
(observed on live data, 2026-08-06).** The two confirmed change events currently recorded
on ad set `120236217374900078` are dated **2026-08-12** and **2026-08-25** — both *after*
that ad set's observed span (`daily_ad_performance` runs 2026-06-06 → 2026-07-31), and
2026-08-25 is in the future relative to today. `_resolve_change_state` / `_change_state_
as_of` only pick up events with `event_date <= day`, so neither event touches a single
observed row: recency and type stay flat, the columns get dropped as zero-variance, and
variables 6/7/9/10 remain absent from the matrix no matter how many events are recorded.
The ad set's start date (2026-06-06) *is* in window, which is why variable 4 works while
the other four don't. This is the `SingleDatePicker` today's-date trap in the paragraph
above, caught in real data rather than in a test: it does not error, it just silently
records a date nothing can join against. **When variables 6/7/9/10 won't appear, check
the event dates against the observed window before touching any code.**

**End-to-end auto-refresh reverified 2026-08-06, on a clean ad set (`120244852135450078`,
no prior confirmed data, to avoid touching real user data a second time).** Recorded a
start date (Jun 10) and an in-window change event (Jun 20–22, budget change) through the
live popover; the Dataset page's correlation matrix grew from 5 to 8 columns in place —
Ad set age, Ad set change recency, Ad set change type all appeared with real correlations
(e.g. age vs. recency = 0.98) — with no page reload. Confirms the `onChange` /
`dataRefreshKey` wiring above actually closes the loop for a user recording data through
the UI, not just for direct API calls. Test data deleted afterward; verified via a
follow-up `GET /api/change-events` that the real two-event record on `120236217374900078`
was untouched by the cleanup this time.

**"Days of the week" showed only six days, no Monday — not a bug, but the display was
misleading, fixed 2026-08-07.** Weekday is one-hot encoded as `weekday_1`..`weekday_6`
(Tuesday–Sunday); Monday (`weekday_0`) is never a feature column at all — it's the
dummy-encoding's structural baseline (every weekday indicator reads 0 on a Monday), the
same collinearity-avoidance reasoning as the change-type groups' reference level above, just
fixed at build time instead of picked dynamically per scope. Variable dictionary's "What it
means" for #8 was building its text as `", ".join(_feature_label(name) for name in in_model)`
over exactly those six features, so Monday could never appear no matter what the data looked
like. Fixed in `_declared_variable_coverage()` (`core.py`): when spec #8 is `in_model`, its
`detail` string now prepends `"Monday (reference)"` before the six fitted weekday labels — so
it reads "Monday (reference), Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday" — since
Monday isn't a fitted term itself, it's excluded from `terms`/coefficients same as before, only
the descriptive label list changed. Also added `"weekday_0": "Monday"` to `_feature_label()`'s
lookup table for correctness elsewhere, though nothing currently calls it with that key (no
feature named `weekday_0` is ever produced by the row builder). Verified live via
`get_ols_model_summaries()['declared_variables'][7]['detail']` and the rendered Variable
dictionary row.

**Superseded same day — Monday is now a real feature, not a labeling patch.** Per explicit
follow-up request ("include each day as its own variable, just like it is defined"), the
"Monday (reference)" display-only prepend above was reverted and replaced with an actual
model change: both weekday row-builders in `_ols_feature_frame` (`core.py`, the historical and
future-horizon loops) now write `weekday_0`..`weekday_6` (`for weekday in range(7)`, was
`range(1, 7)`), `_select_multivariate_ols_features`'s candidate list and `DECLARED_VARIABLES`
spec #8's `features` tuple both gained `"weekday_0"`, and the coverage function's detail-list
special case for spec #8 was removed — `weekday_0` now flows through `in_model`/`varying` and
`_feature_label()` exactly like the other six, no special-casing needed.

**This makes the day-of-week block rank-deficient by construction**, and that's an accepted
tradeoff, not an oversight: with all seven indicators plus the intercept, the seven always
sum to 1 on every row, exactly collinear with the intercept column. `_fit_ols_summary` and
`_fit_ols_predictions` both already build the design matrix as `np.c_[ones, design]` and solve
via `np.linalg.pinv` (not a direct inverse) — this already tolerated the change-type groups'
collinearity (see the top of this file) and handles the weekday case the same way: `pinv`
returns the minimum-norm least-squares solution, `matrix_rank`/`df_model` correctly account for
the lost degree of freedom, and R² is unaffected (verified: 0.5009917992704269, matching the
old 6-dummy encoding's 0.501 up to rounding — same fitted subspace, different parameterization).
The individual day coefficients are **not independently meaningful** as a result — the split
between the intercept and each day's coefficient is arbitrary (whichever the minimum-norm
solution picks), only differences between days carry information, same caveat this file already
documents for change-type group coefficients ("a coefficient of -377 means..."). Deliberately no
`"note"` was added to spec #8 to say so: unlike #4/#6/#7/#9/#10 (which all have one), a note
would take priority over the dynamic in-model day list in the frontend (`item.note ||
item.detail`) and hide the very thing this variable exists to show — the caveat lives as a code
comment on `DECLARED_VARIABLES` and the candidates list instead.

Verified live 2026-08-07: `get_dataset_correlation()` returns all seven weekday columns
(Monday..Sunday) plus weekend; the Variable dictionary row for #8 reads "Monday, Tuesday,
Wednesday, Thursday, Friday, Saturday, Sunday"; the expanded correlation matrix's column
tooltips list all seven under "Days of the week"; `tsc --noEmit` clean. Full test suite has 9
pre-existing failures, all in `ChangeEventEditorTests`/`ChangeLogImportTests`/
`ModelDatasetImportTests` (change-event date validation/editing, e.g. editing an event doesn't
update `end_date` in place) — confirmed unrelated by running only OLS/correlation/weekday-
tagged tests (7/7 pass) and by code-path inspection (those classes never touch
`_ols_feature_frame`, `DECLARED_VARIABLES`, or `_select_multivariate_ols_features`). Flagged
separately, not fixed here.

**"Show Detail" toggle added to the compact OLS cards, 2026-08-07 — full statsmodels-style
printout, reusing calculations already made.** Per request, referencing a statsmodels
`OLSResults.summary()` screenshot. Both compact-mode call sites of `OlsResultCards`
(`coefficients={false}`: the Forecast page's sidebar panel and the Dataset page's OLS cards)
now render a "Show detail" button under the four fit-stat pills. Expanding renders a
new `OlsDetailBlock`: a two-column summary grid (Dep. Variable/Model/Method/Covariance Type/No.
Observations/Df Residuals/Df Model/R-squared/Adj. R-squared/F-statistic/Prob (F-statistic)/
Log-Likelihood/AIC/BIC — all already computed by `_fit_ols_summary`, `OlsResultCards` just
hadn't surfaced them before), the full coefficient table with the CI split into separate
`[0.025` / `0.975]` columns (statsmodels' own layout, vs. the always-visible compact table's
single combined "X to Y" column — a new `.model-gov-ols-detail-table` class, the existing
`.model-gov-ols-table` used elsewhere is untouched), and a residual-diagnostics row.

**New backend fields on `_fit_ols_summary`'s return dict** (`core.py`): `durbin_watson`,
`skew`, `kurtosis`, `jarque_bera`, `jarque_bera_p_value`, `cond_no`. Skew/kurtosis are raw
central moments of the residuals (no scipy dependency, matching this file's existing
no-scipy convention for `_student_t_two_tailed_p_value`/`_f_survival_p_value`). Jarque-Bera's
statistic is chi-squared with exactly 2 degrees of freedom under the null, which has the closed
-form survival function `exp(-x/2)` — no incomplete-gamma implementation needed the way the
t/F tests needed `_regularized_incomplete_beta`. **Deliberately no Omnibus/Prob(Omnibus)** (the
other pair statsmodels shows): that's D'Agostino's K² test, whose exact formula has several
intermediate terms that can go negative/NaN at small sample sizes without careful guarding —
given Jarque-Bera already covers "are residuals normal," the risk of shipping a subtly wrong
Omnibus number outweighed matching the reference screenshot's exact field list. `cond_no` is
`np.linalg.cond(x)` on the same design matrix already built for the fit; on the multivariate
model this reads ~7.3e17 because of the weekday block's intentional rank-deficiency (see the
note above) — `olsCondNo()` in `App.tsx` switches to exponential notation above 10,000 so this
renders as `7.30e+17` instead of a 19-digit integer or a `toFixed`-truncated `0`.

Verified live: both compact-mode instances (Forecast page, Dataset page) show a working
toggle; the Dataset page's Multivariate card's expanded coefficient table lists all 11 terms
(Intercept + spend + holiday + frequency + 7 weekdays) with real Coef/Std err/t/P>|t|/
[0.025/0.975] values; the diagnostics row shows Durbin-Watson 0.690, Skew -0.122,
Kurtosis 2.369, Jarque-Bera 1.069, Prob(JB) 0.586, Cond. No. 7.30e+17; `tsc --noEmit` and
`npm run build` clean; `pytest -k ols` (4/4) still passes with the new dict fields present.
