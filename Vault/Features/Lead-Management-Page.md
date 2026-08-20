# Lead Management page

Built 2026-08-20. A dedicated CRM workspace for the `lead_quality` pipeline stage: rate every
customer, filter the book of leads by campaign / ad set / date / status / search, and read the
funnel and unit economics for whatever slice is on screen.

`lead_quality` itself is older than this page — it was added to `lead_events` on 2026-08-08 and
until now was only reachable through the Forecast page's date-click drill-down (see
[[Lead-Drilldown-Inline-Edit]]), which shows one day at a time. That is the wrong shape for
triaging a book of 3,800 leads, which is what this page exists for.

## What it is

- Nav: **Analyze → Lead Management** (`UserCheck` icon), between Optimization and the Data group.
- `LeadManagementPage` in [App.tsx](../../frontend/src/App.tsx); styles are appended at the end
  of [styles.css](../../frontend/src/styles.css) under a `Lead Management page` banner.
- Six pipeline stages, in `LEAD_QUALITY_OPTIONS` order: Intake (default) → Not Qualified /
  Qualified / Awaiting Document and Payment / Converted / Lost.

## Layout, top to bottom

1. **Filter bar** — campaign, ad set, status, created-date range, free-text search, and a
   Clear button that only appears once something is set.
2. **Funnel** — one card per stage with count, share, and a share bar. Each card is *also* a
   filter toggle (multi-select), so "how many converted?" and "show me the ones that
   converted" are the same click.
3. **Metrics** — Leads in view, Rated, Qualification rate, Conversion rate.
4. **Economics strip** — matched ad set spend, cost per lead / qualified / converted.
5. **Board** — the same Monday-style `board-table` the Dataset page uses, with sort, density,
   column resize, per-page and select-all-matching selection, and a floating bulk bar.

## Decisions worth remembering

- **The funnel and the board share one filter pipeline.** `get_lead_pipeline_summary()` in
  `core.py` calls the same `_dataset_where("leads", ...)` that `get_dataset_rows()` does, and
  `/api/lead-management/summary` takes the identical query params as
  `/api/dataset/rows?table=leads`. A separately written WHERE clause would have drifted the
  first time either side gained a filter field. The frontend builds the params once
  (`queryParts`) and hands the same string to all three endpoints.
- **Only Status and Lead Quality are editable here.** Every other column is imported identity
  the rater is reading *in order to* make the judgement. The Dataset board stays the place to
  correct imported values — same rows, same PATCH endpoint, different job.
- **Rates are over the rated subset, not the whole match.** With 3,800 leads sitting at Intake,
  a whole-set denominator would report a "conversion rate" that mostly measures how much rating
  has been done. `rated = total - intake`.
- **Bulk rating schedules no retrain** — see the gotcha below.
- **Spend is summed over DISTINCT (ad set, day) pairs**, never per lead: a lead row's
  `amount_spent_usd` is the ad set's whole-day spend (see the `leads` spec's COALESCE join in
  `DATASET_ROW_TABLES`), so `SUM()`ing it across leads would multiply each day's budget by
  however many leads that day produced. It is still an upper bound — a day's spend counts whole
  even when a filter matches only some of its leads — and the strip says so on screen.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/lead-management/options` | campaigns, ad sets, date bounds for the filter bar, all derived from `lead_events` so every option returns rows |
| `GET /api/lead-management/summary` | stage counts, rates, and cost figures for the current filter set |
| `POST /api/leads/bulk-quality` | one stage applied to a whole selection, `{lead_ids, lead_quality}` |

Rows come from the existing `/api/dataset/rows?table=leads`, and select-all-matching from
`/api/dataset/row-ids` — neither needed a change. `_parse_filters_param` in `app.py` was
factored out of the two Dataset endpoints while adding the third caller.

## Gotchas found while building this

### `:has()` does not re-evaluate when React swaps a class in place

The edge-to-edge cell fills were originally styled with
`td:has(> .lead-quality-select.quality-converted)` (the rules near the `.lead-quality-select`
block in `styles.css`, added with the column in 2026-08-08). Verified live in Edge: that
selector paints correctly on first render, but when React changes the pill's class on an
already-rendered row the **cell keeps its old background** until a reload. `element.matches()`
returns the new result while the computed background does not — the selector matches, the style
just isn't recalculated.

Harmless on the Dataset board, where ratings are incidental. Not harmless here, where
recolouring on click *is* the interaction. Fix: `LeadManagementPage` puts the state class on the
`<td>` itself (`lead-cell-quality quality-converted`), and `.board-table td.lead-cell-*` rules
restate the fills and inks without `:has()`. Those selectors match only cells this page renders,
so the Dataset board is untouched — **it still has the latent version of this bug.**

### Rating must not trigger a retrain

`lead_quality` is a pure CRM annotation — grep it in `core.py` and outside the schema, the
update allowlist, and this page's own summary query it appears nowhere. Neither
`rebuild_aggregates()` nor `train_models()` reads it. `POST /api/leads/bulk-quality` therefore
deliberately does **not** call `_request_retrain`, unlike every other lead-writing endpoint:
scheduling ~31s of rebuild+train per rating batch would burn CPU recomputing an identical model
(and see [[Retrain-Debounce-And-GIL-Contention]] for what that costs the UI).

The single-cell `PATCH /api/leads/{id}` still retrains, correctly — it is shared with the
Dataset board and can also move `created_at`, `utm_ad_set_id`, or `amount_spent_usd`, all of
which really do feed the aggregates. `status` is a genuine model input too (`rebuild_aggregates`
counts New vs Existing per ad-set-day), so the retrain chip is honest for both editable columns.
The frontend matches this split: `commitLeadField` calls `watchRetrain()`, `applyBulkQuality`
does not.

### The Browser pane lies about paint

Every colour finding above had to be confirmed through Playwright + Edge. The Claude Code
Browser pane wasn't compositing this session, and `getComputedStyle` there returned stale
*background* values while reporting *color* correctly — which initially looked like a CSS
specificity bug that did not exist. See [[Screenshotting-The-App]] and
[[Preview-Pane-Viewport-Unreliable]].

## Verified

Through Playwright + Edge against a real backend, both themes, no console errors:
a row walked through Lost → Not Qualified → Converted → Intake showing four distinct cell
colours **without a reload**; campaign filter (833 leads) with the ad-set picker narrowing to
that campaign; stage-card toggle; `Last month` date preset (1,725 leads, spend recomputed to
$2,154.05, board confined to July); bulk rating 50 leads in one POST with no `/models/retrain`
request and no retrain chip; Clear filters returning to 3,801; funnel stage cards sharing one
count baseline and one bar baseline. All 104 leads touched during testing were restored to
Intake afterwards — the table is back to 3,801 Intake, 0 rated.

Linked from [[Home]].
