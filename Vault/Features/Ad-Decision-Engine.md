# Ad Decision Engine

Built 2026-07-25. Answers "which ad to boost, which to cut." Backend `get_ad_decisions()`
in `backend/core.py`, exposed at `/api/dashboard/ad-decisions` (+ `.csv`).

**Frontend moved 2026-07-28:** the separate Ad Performance page no longer exists — Ad
Performance and Optimization merged into a single Optimization page. `get_ad_decisions`
now also joins each ad set's budget-response curve and emits a combined `action` field
(cut/trim/scale/watch/keep/paused) alongside the original `verdict`; the UI renders
`action`. See [[Budget-Optimization-Tab]] for the reconciliation rules.

**Key modelling decisions, none of which are obvious from reading the code:**
- **Spend and leads come from different tables.** Spend is `daily_ad_performance`
  (Meta export); lead counts are `lead_events` / `daily_ad_set_aggregates`
  (CRM-attributed). The `leads` column *inside* `daily_ad_performance` is only ~18%
  populated and must not be used for CPL — Meta's own count reads ~$11 CPL vs ~$1.28
  actual; the gap is attribution, not a bug.
- **Two adjacent windows** ending at the latest spend date drive the trend (default
  14d vs prior 14d).
- **Verdict thresholds** are ratios against a benchmark CPL: ≤0.75 scale, ≤1.10 keep,
  ≤1.60 watch, above that cut. A strong trend (±25%) moves the verdict one notch.
- **Freed budget is redistributed weighted by 1/CPL** — the cheapest performer absorbs
  the most.
- Ad sets sharing a campaign name get labels suffixed with the last 4 of the ad set ID,
  only when they collide.

**Declared variables 4/6/7/9/10 columns added to the table, 2026-08-06, then blanked
same day.** `get_ad_decisions` first evaluated `days_since_adset_started`,
`ad_change_recency`, `ad_set_change_recency`, `ad_set_change_type`, `ad_change_type`
as of the anchor day per ad set via the same detector helpers the OLS model reads
(`_days_since_start_values`, `_ad_change_features`, `_ad_set_change_features` — see
[[OLS-Declared-Ten-Variables]]). Reported wrong by the user the same day — the
detector-inferred values didn't match reality closely enough to trust. Rather than
remove the 5 columns, `get_ad_decisions` now hardcodes all five to `None` per ad set
(they're pending real recorded data, not a better detector); the frontend's existing
`?? '—'` / `formatChangeType(null)` fallbacks render the blanks with no UI change
needed. See [[Dataset-Page]] for the matching revert on the raw-row browser. Not on
the CSV export (`/api/dashboard/ad-decisions.csv`) — table only, and now empty there
too.

**The wrongness went deeper than these two display tables — the underlying detectors
themselves were removed 2026-08-06,** once the user realized the correlation matrix,
OLS fit, and forecast model were still trusting the same inferred values this table
had just stopped showing. See [[OLS-Declared-Ten-Variables]] for the full fix: the
step-shift and per-ad-activity detectors are gone from `backend/core.py` entirely, not
just hidden here. This table's hardcoded `None`s were left as-is (not reverted to call
the now-safe confirmed-only functions) — a 0 from "confirmed data exists but says
zero" and a 0 from "nothing confirmed yet" would look identical on this table, so it
stays blank until that ambiguity is worth resolving.

Related: [[Forecast-Flatness-Fix]], [[Budget-Scenario]], [[Preview-Pane-Viewport-Unreliable]],
[[OLS-Declared-Ten-Variables]].
