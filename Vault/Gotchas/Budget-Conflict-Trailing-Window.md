# Budget Conflict Detection Uses a Trailing Window

Budget Scenario flagged ad sets where mean spend exceeded their stated daily budget,
but inspection found the flagged ad sets all overspent early (Meta's learning phase)
then settled onto budget.

**Fix:** conflict test now judges only the trailing 14 days, not the full period. An
ad set that ramped down over 7 weeks now reads as compliant instead of permanently
conflicted. Result: 3 conflicts → 1, and recent means cluster within pennies of stated
budgets — validating that the `Ad Set Budget` column is real and delivery tracks it.

**Why:** Meta's daily budget is a pacing target, not a hard daily cap — new/edited ad
sets overspend 50-100% for ~7-10 days while delivery calibrates, then converge. The old
full-window test hid convergence; the 14-day tail answers what matters: is it over
*now*?

Code: `BUDGET_CONFLICT_RECENT_DAYS = 14` in `backend/core.py`. Related:
[[Leadlens-Ad-Export-Grain-And-Budget]], [[Budget-Scenario]].
