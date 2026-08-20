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

1. **Bento** — four cells for four questions (see the redesign section below).
2. **Toolbar** — one compact row directly above the board: search, campaign, ad set, lead
   quality, created range, Clear, then a hairline rule, then Sort and row height.
3. **Board** — the same Monday-style `board-table` the Dataset page uses, with sort, density,
   column resize, per-page and select-all-matching selection, and a floating bulk bar.

## Decisions worth remembering

- **The funnel and the board share one filter pipeline.** `get_lead_pipeline_summary()` in
  `core.py` calls the same `_dataset_where("leads", ...)` that `get_dataset_rows()` does, and
  `/api/lead-management/summary` accepts the identical query params as
  `/api/dataset/rows?table=leads`. A separately written WHERE clause would have drifted the
  first time either side gained a filter field. **What the frontend sends them differs by
  design though** — the summary gets scope only, not the stage toggles. See "The funnel
  describes the SCOPE" below; an earlier version of this note said all three endpoints got the
  same string, which was true of the first cut and is no longer.
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

## The bento (redesign, 2026-08-20)

The first cut of this page was three horizontal bands of equal tiles: six stage cards, four
`Metric` cards, four cost figures. Fourteen numbers at identical visual weight, so nothing led
and the squint test returned nothing. Reworked the same day into a four-cell bento, keeping the
app's own dark gold / cream world and borrowing only the reference's structural habit of
letting one object lead.

```
+---------------------------+-------------+
|                           |  Awaiting   |
|   Pipeline  (spans 2)     |  review     |
|                           +-------------+
|                           |  Outcomes   |
+---------------------------+-------------+
|   Acquisition cost (full width)         |
+-----------------------------------------+
```

- **Pipeline** leads, because "where is my book stuck" is what this page is for. One segmented
  bar carries the whole distribution at a glance; under it the six stages are compact rows,
  each a filter toggle.
- **Awaiting review** shows the share reviewed and how many are left.
- **Outcomes** carries the two rates, and an empty state before anything is rated.
- **Acquisition cost** runs full width with the four money figures and the attribution caveat.

Collapses to `pipeline / queue + outcomes / cost` under 1120px, then to a single column under
780px.

### What the redesign fixed, and why

- **Redundancy.** The old layout printed the lead total twice ("Intake 4,032 / 100%" and "Leads
  in view 4,032") and said "nothing has been rated" three different ways. The bento says each
  thing once.
- **The `Metric` cards had fake sparklines.** `Metric` draws one of five hardcoded decorative
  squiggle paths picked by `index % 5` — a trend line for data that was never measured. This
  page no longer uses that component. (The component is unchanged and still used elsewhere;
  worth knowing what those squiggles are.)
- **The stage list is one column, in pipeline order.** Two columns fit in less height, but CSS
  grid fills row-major, so scanning *down* the first column read Intake, Qualified, Lost —
  three stages that are neither consecutive nor a sequence.
- **"Awaiting review" shows a share, not a count.** Until someone rates a lead, "leads still at
  Intake" *is* the pipeline total, so the two headline numbers sat side by side printing the
  same figure. A share can never echo a count.
- **The outcome band is labelled "Passed qualification", not "Qualified".** It counts Qualified
  + Awaiting Document and Payment + Converted, which is a bigger number than the Qualified
  *stage* listed a few inches away in the same viewport. Both reading "Qualified" made the page
  look like it contradicted itself.
- **Cents are held back a step** (`.lead-figure-cents`). Four dollar figures side by side read
  as eight numbers when the cents carry the same weight as the dollars.
- **The campaign / ad set selects are capped** at 260px / 330px. `flex: 1 1 auto` let them
  stretch past 600px on a wide screen, which put each label a hand-span from its value.

### One toolbar instead of a labelled filter panel (2026-08-20, same day)

The scope filters started as a labelled panel above the bento: five fields, each with an
uppercase caption over it. It cost about 90px of vertical space to print "CAMPAIGN" above a
control that already read "All campaigns", and the two greedy selects (`flex: 1 1 auto`)
stretched past 600px on a wide screen, putting each caption a hand-span from its own value.

Now they sit in the board toolbar, in the Dataset page's toolbar vocabulary — so the app's two
data surfaces are operated the same way:

    [ 3,801 leads in this view ]      [search] [Campaign v] [Ad set v] [Quality v] [All time v] [x Clear] | [Sort v] [rows]

- The captions are gone because every control names itself when nothing is picked ("All
  campaigns", "Any status", "All ad sets") and carries a leading icon once something is.
- An applied filter turns gold, matching `.board-tool-btn.is-active` beside it, so "is anything
  filtered?" is answerable without opening a menu.
- A hairline rule separates scope (which leads are under discussion) from board tools
  (re-ordering the ones already chosen). Different jobs, so proximity should not merge them.
- `MenuSelect` gained an optional `short` per option: the menu still renders the full `label`
  (campaign name plus lead count, ad set id plus title plus count) while the closed trigger
  shows a short form. Without it a picked campaign truncated to "Leads | VISA | AU | KHM (8...",
  and a cut-off count is worse than no count. Optional field, so every other caller is
  unaffected.
- **The third pill filters lead quality, not status** (changed on request, same day). It was
  New / Existing; the page exists to work the quality pipeline, so that is what the toolbar
  filters. There is no status filter on this page any more — `statusFilter` and its filter row
  were removed rather than left as dead state. Status is still editable per row on the board,
  and still filterable on the Dataset page's Leads tab.
- **The quality pill and the stage cards are one control, two surfaces.** Both read and write
  `qualityFilter`, so picking a stage in the dropdown lights its card and toggling a card
  updates the trigger. `MenuSelect` is single-select, so choosing a stage *replaces* the
  selection while the stage rows remain the way to build a multi-stage one; when more than one
  is active `value` falls through to the "Any quality" option, whose `short` then reports
  "N stages". The trigger must never read "Any quality" while two stages are filtered.

The toolbar now sits *below* the bento it scopes. That is deliberate — it is the board's
toolbar, and the Dataset page puts it in the same place — and the pipeline cell prints the
active scope in its own header note, so the bento always states what it is counting.

Adaptation checked at 1280 / 1024 / 820px: one toolbar row down to 1024, wrapping (not
squeezing) at 820, no horizontal overflow at any width.

### The funnel describes the SCOPE, not the stage selection

The most important behavioural fix, and the one that is easiest to reintroduce by accident.
`LeadManagementPage` builds two param sets:

- `scopeParts` — campaign, ad set, date, status, search. **This is what
  `/api/lead-management/summary` gets.**
- `queryParts` — `scopeParts` plus the `lead_quality` filter. This is what the board and
  `/api/dataset/row-ids` get.

Sending the stage toggles to the summary as well made the pipeline cell a tautology: clicking
Converted made it read "268 leads, 100% Converted", destroying the context that made the click
worth making, and zeroing the other five stages so there was nothing left to compare against or
add to the selection. The summary effect is keyed on `scopeKey`, deliberately *not* `queryKey`,
so a stage toggle does not even refetch it. Selected stages stay lit in the bar; the rest drop
to `opacity: .22`.

### Motion

One authored moment: the review-progress fill, animated with `transform: scaleX()` rather than
`width`, which would force a layout pass. The pipeline segments have **no** width transition on
purpose — a single rating moves them by a fraction of a percent, and a filter change swaps in a
different population altogether, where morphing one distribution into the other would imply a
continuity that does not exist.

### Verified (redesign)

Real Edge through Playwright, both themes: WCAG AA contrast on all ten small-text roles;
the pipeline holding at 3,801 across a stage toggle with all six rows still populated;
multi-select stacking (Qualified 430 + Converted 268 = a 698-row board); keyboard order matching
pipeline order; `detect.mjs` clean over this page's code; no console errors. The 1,559 demo
ratings used to review the populated state were restored to Intake afterwards.

**Measuring contrast on this app has two traps, and both manufacture false failures.** Between
them they produced three "AA failures" here that did not exist:

1. `--surface-soft` is `var(--surface-tint)`, an `rgba()` overlay. Read as an opaque background
   it reports the old filter labels at 2.6:1 and 3.0:1; composited onto the canvas first they
   are 6.8 and 5.2.
2. `color-mix()` computes to `color(srgb 0.96 0.95 0.93)` in Chromium — **0-to-1 floats, not
   0-to-255**. A parser written for `rgb()` reads that near-white tint as near-black and reports
   the gold scoped-filter pill at 3.53:1 when it is really 5.34:1.

`scratchpad/contrast_final.py` in the session that wrote this handles both. Composite the
layers and check the color space before believing a number.

One *real* failure did surface once the parser was fixed: on an `.is-active` stage row the tint
lifts the background and `--dim` fell to 4.03:1 in dark. That row's percentage now steps up to
`--muted`.


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

## Verified (initial build)

Through Playwright + Edge against a real backend, both themes, no console errors:
a row walked through Lost → Not Qualified → Converted → Intake showing four distinct cell
colours **without a reload**; campaign filter (833 leads) with the ad-set picker narrowing to
that campaign; stage-card toggle; `Last month` date preset (1,725 leads, spend recomputed to
$2,154.05, board confined to July); bulk rating 50 leads in one POST with no `/models/retrain`
request and no retrain chip; Clear filters returning to 3,801; funnel stage cards sharing one
count baseline and one bar baseline. All 104 leads touched during testing were restored to
Intake afterwards — the table is back to 3,801 Intake, 0 rated.

Linked from [[Home]].
