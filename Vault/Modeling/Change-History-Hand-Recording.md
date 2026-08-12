# Change history, hand-recorded for all 29 ad sets

Started 2026-08-11. The change-event tables had been **empty** since they were built — every
declared variable that depends on them (6, 7, 9, 10) was therefore inert, contributing nothing
to any fit. This note tracks the manual backfill and the two design changes it forced.

See [[OLS-Declared-Ten-Variables]] for what the variables are and
[[Dataset-Page]] for where they surface in the UI.

## Input format

The history arrives ad set by ad set, dated events, several per ad set:

```
1. 120236217374900078:
ad_set_change_recency: Jul 4, 2026
ad_set_change_type: budget_increase
ad_change_recency: Jul 4, 2026
ad_change_type: ad_added
ad_change_recency: Jul 9, 2026
ad_change_type: ad_added
```

A repeated `recency`/`type` pair is a second event, not a correction. Note the field named
`*_recency` carries an **event date** — recency itself is derived, never typed by hand.

## Two design changes this forced

**1. `budget_change` split into `budget_increase` / `budget_decrease` (2026-08-11).**
*(Reverted the same day — change type was deleted outright a few hours later. Kept for the
reasoning, which is why the directional question came up at all.)*
`AD_SET_CHANGE_TYPES` went from 4 to 5 members. A raise and a cut move leads in opposite
directions, so a single pooled indicator averages the two effects toward zero and hides both.
`change_events` was empty at the time, so no migration was needed. Consequence:
`CHANGE_TYPE_FEATURES` is now 9 dummies (5 ad-set + 4 ad), up from 8. The tradeoff accepted
knowingly: each type fires on ONE day per event, so splitting one thin dummy into two thinner
ones raises overfitting risk on a dataset already known to be flat
([[Forecast-Flatness-Is-The-Data]]). Direction was judged worth more than density.

Touched: `AD_SET_CHANGE_TYPES` and the `_ols_feature_label` map in `backend/core.py`, the
variable-9 `partial` blurb, and `CHANGE_SCOPES` in `App.tsx`. The offline builders under
`Dataset/Derived Variables/` and `build_model_template.py` still say `budget_change` — they are
historical one-off scripts, not the live pipeline, and were deliberately left alone.

**2. Recency becomes a 5-level categorical, not a day count.**

| days since most recent event | bucket |
| --- | --- |
| 0–3 | `0_3_days` |
| 4–7 | `4_7_days` |
| 8–14 | `8_14_days` |
| 15–59 | `15_59_days` |
| ≥60, or no prior event | `no_recent_change` |

`recency_bucket()` in `backend/core.py` is the single source of truth — edges are inclusive
upper bounds, contiguous, so every non-negative day lands in exactly one. Boundaries
unit-checked at 0/3/4/7/8/14/15/59/60.

**Shipped 2026-08-11 for the raw table** (`_change_state_as_of`, which feeds
`_attach_declared_variables` and so the Dataset page's Raw data board). Its return type went
`int | None` → `str | None`. It has exactly one caller, so nothing else moved.

**Still to do for the model.** `_resolve_change_state` — the function the OLS and correlation
matrix read — still returns a continuous float. When that switches, each of variables 6 and 7
becomes 4 dummies + `no_recent_change` held out as reference, taking the two from 2 columns to
8. Decided alongside: the OLS gets the dummies; the **correlation matrix gets ordinal codes
0–4** (see `RECENCY_BUCKETS`, ordered for exactly this), which keeps the declared matrix at
10×10 rather than exploding to 16, and is defensible because the scale is monotone in
time-since-change. Deferred until all 29 ad sets are recorded — it is a one-time change and
retraining is ~18s a run.

Note this means the **table and the model still disagree** on variable 6/7 encoding: the table
buckets, the model reads the raw day count. That gap survived the type removal and is still
open. `_change_state_as_of`'s docstring promises it mirrors `_resolve_change_state`, so closing
it is not optional — it is the main outstanding item once the 29 ad sets are in.

**Bucketing fixes a real conflation.** In the continuous encoding "no event ever" and "changed
today" both compute to `0.0` and are indistinguishable. They split into `no_recent_change` vs
`0_3_days`. Expect existing OLS numbers to move when the model side lands — that is the bug
leaving, not a regression.

**Three states, not two, in the raw table.** An ad set with no recorded events of a scope
renders `-` (None), *not* `no_recent_change`. `no_recent_change` is a claim the change log
makes about a day; `-` means nobody has recorded that ad set either way. Collapsing them would
let an untouched ad set look fully audited — currently 17 of the 18 spend-carrying ad sets are
in the `-` state, so this is most of the table.

## Raw table shows identifiers, not prose

Also 2026-08-11, same request. The board's four change columns render the stored value verbatim
(`budget_increase`, `0_3_days`, `no_change`) instead of the title-cased prose
`formatChangeType` produces ("Budget Increase", "No Change"). New `rawCategory` helper in
`App.tsx`; the recency columns also lost `align: 'num'`, since right-aligning `no_recent_change`
against `0_3_days` reads ragged. Consistent with those headers already being raw column names
after the [[Dataset-Page]] relabel — a value reading "No Change" under a header reading
`AD_CHANGE__TYPE` can't be matched against the model's category by eye.

`formatChangeType` is **kept** and still used by the Optimization page's decision table, which
is a prose surface for a different audience. Only the Dataset board changed.

## Only 18 of the 29 ad sets can carry these features

`daily_ad_performance` holds 18 distinct ad sets; `lead_events` and `daily_ad_set_aggregates`
hold 29. The declared variables attach to ad-set-**day** rows, which only the 18 have, so a
change event recorded against the other 11 stores fine and stays inert as a feature. Two of the
11 are not trivial: `120237359038760078` (50 leads) and `120246731559360078` (25 leads). Same
silent-inertness family as the out-of-window dates noted in [[Change-Event-UI-Recorder]].

## Progress

Window is 2026-06-06 → 2026-07-31.

- **1. `120236217374900078`** — recorded 2026-08-11, ids 19/20/21. ad_set `budget_increase`
  Jul 4; ad `ad_added` Jul 4 and Jul 9. Verified the derived buckets, including the Jul 9
  reset on the ad clock (`4_7_days` → `0_3_days`).
- **2. `120238338920760078`** (461 leads) — recorded 2026-08-11, id 22. ad_set
  `budget_increase` Jul 9. **Ad scope: nothing recorded** — the source said
  `no_recent_change`, which is not a storable value (see below), so the ad columns render `-`.

**Ad sets 1 and 2 were redone 2026-08-11** against fuller date lists reaching back to April.
Nothing was deleted: the already-stored rows were exactly the typed subset of the new lists, so
the missing dates were added untyped (ids 27–34 for ad set 1, 35–42 for ad set 2) and the known
Jul 4 / Jul 9 types were preserved. Net effect:

- **ad set 1** — June flipped from `no_recent_change` to **`15_59_days`** on both scopes,
  because May 12 is 25–52 days before those days. This is the whole reason pre-window dates
  are worth recording.
- **ad set 2** — ad scope now reads `15_59_days` Jun 6–Jul 12 then decays to
  **`no_recent_change`** from Jul 13, which is May 14 + 60. First live demonstration of the
  ≥60-day rule. Its ad scope had been recorded as "no changes" the round before; the new list
  supersedes that, which is why the audit-marker idea below was never implemented.

- **3. `120237272609950078`** (280 leads) — recorded 2026-08-11, ids 23/24/25 via
  `POST /api/change-events`. ad_set `budget_increase` Jul 4; ad `ad_swapped` Jun 6 and Jul 17.
  First ad set recorded through HTTP rather than a script — the event appeared immediately
  with no uvicorn restart, confirming the cache fix above. Also the first event landing on
  **the first day of the window** (Jun 6): harmless under point semantics, it fires that day
  only, but it means ad scope never shows `no_recent_change` for this ad set. Under the old
  carry-forward encoding this same case produced a constant column that was silently dropped
  from the fit — see [[OLS-Declared-Ten-Variables]].

## Change type deleted entirely, 2026-08-11

Superseding everything in the next section. Per request, declared variables **9
(`ad_set_change_type`) and 10 (`ad_change_type`) were removed from the model and from every
place in the app**, and the `change_type` column was dropped from `change_events` along with
its seven recorded values. The declared set is 1-8. Backup first:
`data/leadlens.db.bak-before-type-removal-20260811-132204`.

**All 23 event DATES survived** — recency was never derived from the kind, only from "when did
something last change", so variables 6 and 7 are byte-identical before and after. Verified on
all three recorded ad sets.

What went, in order of blast radius:

| Layer | Change |
| --- | --- |
| Model | `CHANGE_TYPE_FEATURES` (9 dummies), `CHANGE_TYPE_GROUPS`, `_change_reference_categories` — the whole held-out-reference mechanism, which existed only for these dummies |
| Feature builders | `_resolve_change_state` returns a bare recency array, not `(recency, indicators)`; `_ad_change_features` / `_ad_set_change_features` each emit one key |
| Event reader | `_recorded_change_events` returns `tuple[Timestamp, ...]`, not `(day, kind)` pairs |
| Table derivation | `_change_state_as_of` returns one bucket string, not a pair |
| OLS panel | Variable dictionary entries 9 and 10, their nine `_feature_label` entries, the `flat_recorded` status branch (which only ever fired for type groups) |
| API | `GET /api/change-event-types` deleted; `change_type` off the `ChangeEvent` payload and off `save_change_event`'s signature |
| UI | Type columns off the Dataset board and the Optimization decision table; the entire type picker (trigger, portaled menu, positioning `useLayoutEffect`, outside-click effect, `changeTypeLabel`, `formatChangeType`) and all its CSS |
| Schema | `ALTER TABLE change_events DROP COLUMN change_type`, guarded for SQLite < 3.35 |
| Dead code found on the way | `_ad_change_event_days` — a per-ad detector, defined and cache-cleared but never called, returning type pairs. Removed with its `AD_PAUSED_STATUSES` constant. |

**Uploads still accept a change-type column and always will** — the sheets have one. It is now
read for exactly one purpose, telling "a change happened here" from "I checked, nothing
changed" (`UPLOAD_BASELINE_VALUES`), and then discarded. A side effect worth knowing: an
unknown value like `colour_change` is no longer rejected. Any non-blank, non-baseline cell
marks an event, so a sheet still using the pre-removal vocabulary imports its dates cleanly
instead of failing. The `changelog_*:unknown_change_type` skip reason no longer exists.

Retrained after the change: run 248, 58 forecasts, completed. Suite at **132 passed**.

### Superseded: untyped events

*The section below described `UNTYPED_CHANGE`, added earlier the same day so dates could be
recorded ahead of types. It was removed a few hours later along with types themselves. Kept
because it explains why the recorded dates look the way they do.*

From the second pass onward the source supplies **dates only**. Per instruction ("leave blank
for the types for now"), `UNTYPED_CHANGE = ""` was added 2026-08-11 rather than guessing a
category or inventing an `unspecified` one.

An untyped event is a real, stored fact that:
- **drives** variables 6 and 7 — the date alone determines how long ago something changed;
- **fires nothing** in variables 9 and 10 — `_resolve_change_state` only sets an indicator for
  a kind present in the scope's tuple, and `""` is deliberately absent, so the OLS never fits
  a category nobody stated.

Filling the real type later is a plain update of the same row. Three code points:
`UNTYPED_CHANGE` const, the vocabulary filter in `_recorded_change_events`, and the guard in
`save_change_event`. A **typo still 422s** — only exactly-blank passes, verified by probe
(`budget_increse` was rejected, `""` accepted).

The raw table now has **four** states in the type columns, and the first two are easy to
confuse:

| cell | means |
| --- | --- |
| `-` | nothing ever recorded for this ad set in this scope |
| *(blank)* | a change IS recorded this day, kind not supplied yet |
| `no_change` | the log positively asserts nothing changed this day |
| `budget_increase`, … | the recorded kind |

`rawCategory` in `App.tsx` maps `null`→`-` and passes `""` through as an empty cell.
**Caveat:** every untyped event recorded so far falls outside 2026-06-06..2026-07-31, so zero
in-window rows currently show a blank cell — that rendering is logic-verified but has not yet
been seen on a real row. First in-window untyped event will be the real test.

## Pre-window dates matter; post-window dates do not (yet)

The redo lists reach back to April. Worth being precise, because it is counterintuitive:

- A date **before** the window still drives recency, since `_recorded_change_events` reads
  every row for the ad set regardless of window and `_change_state_as_of` takes the most
  recent prior event. Its *type* never fires, because no in-window day equals it.
- A pre-window date that is **superseded** by a later pre-window date has no effect at all.
- A date **after** the window affects nothing today, but is worth storing for when data
  extends past it.

Concretely on ad set 1: of 11 supplied dates only 3 fire a type, 2 (the May 12 pair) drive
recency, and 6 do nothing to current numbers. All were recorded anyway — the audit trail is
the point, and the inert ones become live as the window grows.

### "no_recent_change" as an input value (historical)

Ad set 2 is the first case of the source asserting `no_recent_change` for a whole scope. That
is an audit claim — "I checked, no ad changes happened" — and the system has nowhere to put it:
`save_change_event` rejects every value in `BASELINE_CHANGE_VALUES` because under point-event
semantics an unrecorded day already means "no change".

So the ad columns show `-`, which in the table's three-state vocabulary means *unrecorded*, not
*audited-and-clean*. The distinction is invisible to the model (both are baseline zeros across
every dummy), so this is purely about whether the table can show its own coverage. Left as `-`
for now, flagged to the user. If it matters, the options are: infer coverage from "this ad set
has ≥1 recorded event in any scope" (no schema change, but wrong for an ad set audited in only
one scope through the popover), or add a real audited-marker table.

## Record through the HTTP API, not a standalone script

`_recorded_change_events` is `lru_cache`d. Writing with `core.save_change_event()` from a
separate `python -c` process clears only **that** process's cache — the running uvicorn keeps
serving its own stale entry, so the new event silently does not appear in the table or the
model. This bit ad set 2: the row was in SQLite and the page still showed `-` on every day.

It only looks fine if the server has never queried that ad set yet (ad set 1 got lucky — it
was recorded before the Dataset page had been loaded, so there was no cache entry to go stale).
Once the page has rendered, every ad set on it is cached.

Use `POST /api/change-events` instead. `save_change_event` runs inside the server process
there, so its `cache_clear()` actually lands, and the endpoint also fires the guarded
background retrain. Recovery if a script was used anyway: touch any file under `backend/` to
make watchfiles restart uvicorn, which rebuilds the caches from SQLite.

## Gotcha found on the way in

`save_change_event` returns `start_date`/`end_date`, **not** `event_date` — despite the column
being `event_date` and the docstring talking about event dates. Reading the wrong key raises
`KeyError` after the row has already been written, so a naive retry double-writes. It is
idempotent per `UNIQUE(scope, event_date, ad_set_id, ad_id)`, so the damage is contained, but
check the DB before re-running a partially failed batch.

## Test suite was already red here — stale tests rewritten 2026-08-11

Nine tests in the change-event suite failed independently of this work, verified 2026-08-11 by
reverting the type change and re-running. They asserted behaviour deliberately removed on
2026-08-06 (step-shift detector) and 2026-08-07 (point events, baseline rejected). Production
code was correct; the tests had never been updated. Swapping the `budget_change` literal in
tests introduced **zero** new failures — before and after lists were identical.

**Resolved by rewriting the tests, not the production code** — `tests/test_pipeline.py` only.
`tests/test_pipeline.py -q` now reports **131 passed, 0 failed** (130 + one new test). What
each stale assertion became:

| Test (new name) | Was asserting | Now asserts |
| --- | --- | --- |
| `test_only_a_confirmed_log_produces_events_there_is_no_detector` | a spend step infers a budget change | a spend step infers **nothing**; only a recorded row fires, and an unrecorded ad set stays all-zero |
| `test_recency_counts_from_the_recorded_event_and_the_type_fires_once` | type carried forward after the event | recency carries forward, the type fires on its own day only |
| `test_a_no_recent_change_row_is_skipped_not_stored` | a baseline row is a legal stored entry | it is skipped and counted as `changelog_ad:no_change_rows` |
| `test_a_file_of_baseline_rows_only_holds_no_usable_events` | *(new)* | a changelog of nothing but baselines is rejected at preview |
| `test_filled_change_columns_become_confirmed_events` | state-dedup collapsed repeat values | two same-type events on consecutive days are two events; a `no_change` cell records nothing |
| `test_a_change_is_stored_as_a_point_event_on_its_own_day` | `end_date` keeps the posted range end | `end_date` is collapsed to `event_date` |
| `test_each_scope_only_accepts_its_own_categories` | baseline recordable at ad scope | baseline rejected at **both** scopes, for both spellings in `BASELINE_CHANGE_VALUES` |
| `test_a_missing_or_unparseable_date_is_rejected` | a reversed range raises | unparseable/blank date and blank ad set still raise; a reversed range is accepted and the end date discarded |
| `test_editing_updates_in_place_and_deleting_leaves_no_event_behind` | edit keeps the new range end; delete restores the detector | edit collapses onto the event day; delete leaves nothing (no detector to restore) |
| `test_coverage_counts_the_live_days_that_carry_a_recorded_change` | a 4-day range covers 4 days | the point event covers 1 day, 9 uncovered |

Design rationale for every one of these lives in [[Change-Event-UI-Recorder]] ("A change is a
point event, not a range") and [[Ad-Decision-Engine]] (detector removal) — the rewritten tests
carry short comments pointing back at the same reasoning.

## Backfill complete — final OLS, 2026-08-11

All 26 ad sets have change history; all 26 have a launch date. Variables 4, 6 and 7 carry real
data for the first time. Retrain run 302, 52 forecasts. Portfolio scope, 56 daily obs.

**Ad sets 26/27/28 were deleted, not recorded** — `120211524648810078`, `120213709832400078`,
`120228963850300078`, one lead each, no performance rows. 25,228 rows removed across
`lead_events` (3), `daily_ad_set_aggregates` (3), `forecasts` (994),
`forecast_daily_predictions` (6,958) and `model_backtest_metrics` (17,270). Dataset went
29 ad sets / 3,023 leads to **26 / 3,020**. Backup:
`data/leadlens.db.bak-before-adset-delete-20260811-143408`.

### The numbers

| | R2 | adj R2 | RMSE | AIC | BIC |
| --- | --- | --- | --- | --- | --- |
| Spend only | 0.485 | **0.475** | **11.79** | **437.2** | **441.2** |
| Multivariate (8 vars) | **0.581** | 0.464 | 11.91 | 449.6 | 478.0 |

Multivariate against its own pre-backfill state: R2 0.501 -> **0.581**, adj R2 0.403 ->
**0.464**, RMSE 12.61 -> **11.91**. The gain is entirely from filling 4/6/7.

Newly significant: `ad change recency` +1.975 (p=0.0069), `ad set age` -0.844 (p=0.026),
`ad set change recency` -0.629 (p=0.043). `spent` +0.732 (p<0.0001). Everything else
non-significant; `frequency` is -3.59 at p=0.945.

### Read these with three caveats

1. **The declared set still loses to spend alone** on every penalised measure. Raw R2 is higher
   only because 11 extra parameters bought it. The gap narrowed a lot but did not close, which
   is consistent with [[Forecast-Flatness-Is-The-Data]] rather than a refutation of it.
2. **Durbin-Watson 0.84** — strongly autocorrelated residuals, so standard errors are
   understated and those three p-values are optimistic. Promising, not established.
3. **The model reads RAW DAY COUNTS, not the buckets.** The encoding gap is still open: the
   Dataset board shows `0_3_days`/`4_7_days`/..., the OLS fits a continuous "days since". So
   `+1.975` is per day. Note the two recency coefficients point in **opposite directions**
   (+1.98 ad, -0.63 ad set) — bucketing may clarify that or expose it as noise.

`cond_no` 1.7e18 is the seven weekday dummies being collinear with the intercept by design
(see [[OLS-Declared-Ten-Variables]]); individual weekday coefficients are not independently
interpretable, only their differences.

### Launch dates

26 recorded. Cohorts: Feb 2025 x1 (482 days old at window start), Nov-Dec 2025 x8 (160-202),
Apr-May 2026 x6 (26-37), Jun-Jul 2026 x2 (**0** — window opens on launch day). Previously every
age was left-censored to the first upload date, understating the older ad sets by months.

Three entries arrived as **Nov 16, 2026** — a future date, which blanks the column entirely.
Corrected to 2025 on confirmation; the `notes` column on those three rows records the change.
One asymmetry left un-actioned: `...67600078` is Feb 9 **2025** while sibling `...67570078` is
Feb 9 **2026**, making the former a 482-day outlier against an otherwise Nov-2025-onward range.
Flagged to the user, not altered.
