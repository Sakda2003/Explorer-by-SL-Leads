# Budget Optimization Tab

Built 2026-07-28, extended same day. Answers "when this ad set's budget changed, did
its cost per lead go up or down?" — distinct from [[Ad-Decision-Engine]] (grades ad
sets against a portfolio benchmark). Backend: `get_budget_optimization()` in
`backend/core.py`, `/api/dashboard/budget-optimization`. Reuses `ad_set_budget_periods`
from [[Budget-Scenario]] — no new tables.

**Critical finding: Meta's "Ad Set Budget" export field is a snapshot, not a
history.** `daily_ad_performance.ad_set_budget` is perfectly flat across every ad
set's entire recorded history, even though actual daily spend clearly step-changes.
So the column-watching derivation can never detect a real historical budget change.
**This is the thing to remember if this signal ever looks broken again: don't trust
`ad_set_budget`, trust the spend trajectory.**

**Fix — spend-based changepoint detection** (`_detect_spend_changepoint()`): single
strongest sustained level-shift only, conservative thresholds (≥7-day segments, ≥35%
shift). Used only as a *fallback* when recorded/derived periods are insufficient;
synthesized periods never get written back to `ad_set_budget_periods`, so they can't
collide with manual entries. Each response carries
`budget_basis: "recorded"|"detected"|"unknown"`.

**Two elasticities:** `fitted_elasticity` (leads-vs-budget, clamped [0.20,1.0], feeds
the forecast formula) and `cpl_elasticity` (CPL-vs-budget, unclamped — sign is the
whole signal: positive = decrease, negative = increase, dead zone ±0.15 = hold, later
widened to **0.5** after a real threshold bug flagged healthy scaling as saturation).

**Pages merged 2026-07-28** — Ad Performance and Optimization were the same job.
`get_ad_decisions` now joins the budget data server-side and collapses both signals
into one `action` via `_combine_action`: cut/paused pass through, a plateaued ad set
becomes `trim` regardless of cost, everything else keeps its benchmark verdict.

Money math: trimmed budget joins the reallocation pool but is not charged the cut-CPL
lead loss (that money had already stopped converting). Only `scale` ad sets receive
reallocated budget.

Layout gotcha (fixed): page roots must use `.page-content` — a page shipped with a
classless `className="page"` and rendered full-bleed with controls flung to the corner.
