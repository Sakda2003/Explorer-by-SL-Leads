# Lead-Level Spend Fallback To Ad Performance

Fixed 2026-08-14. Symptom: the Dataset page's "leads" raw table (Amount column) showed
`-` for every lead from 2026-08-01 onward, while earlier leads showed a dollar value.

**Root cause.** `lead_events.amount_spent_usd` was never spend *belonging* to that
lead — CRM traffic exports don't carry a per-lead dollar figure. Values that used to
show up there only arrived as a side effect of the lead-grain model-dataset workbook
(see [[Model-Dataset-Upload-Type]]), which repeats the ad set's whole-day spend onto
every lead row on that day as context. Once uploads switched to the plain
`customer_traffic` + `ad_performance` export pair (2026-08-01 onward, `traffic_*.xlsx` /
`Ad-Set-Performance-and-Traffic-*.xlsx`), no per-lead spend column existed and nothing
filled the gap — even though `daily_ad_performance` had the real spend for those same
(day, ad set) pairs all along.

**Fix.** Two places now fall back to `daily_ad_performance` (summed per ad_set_id/day)
whenever the lead-level value is missing:
- `rebuild_aggregates()` in `backend/core.py` — `daily_ad_set_aggregates.spend_context_usd`
  (feeds the forecast spend signal, see `_aggregate_spend_values`).
- `DATASET_ROW_TABLES["leads"]` — a `LEFT JOIN` subquery grouped by `(ad_set_id, day)`,
  `COALESCE(amount_spent_usd, p.spend)`, used in both the row query and its filter
  field so filtering/sorting by Amount still works (the COUNT query had to start
  including the join too, since a filter can now reference the joined alias).

Re-running `rebuild_aggregates()` against the existing DB recovered
`spend_context_usd` for Aug 1–12 immediately; no re-upload needed. **The running
backend process must restart** to pick up the code change — `uvicorn` here isn't
running with `--reload` (see [[Uvicorn-Reload-Hangs]] for why that's off).

**Why:** the model-dataset workbook and the plain export pair are both legitimate
upload paths and can be mixed over time; spend context shouldn't silently disappear
just because a given week's upload happened not to be the lead-grain workbook.

Related: [[Model-Dataset-Upload-Type]], [[Ad-Decision-Engine]], [[Leadlens-Ad-Export-Grain-And-Budget]].
