# UI component inventory (for the visual redesign)

Built 2026-08-12, ahead of a planned redesign — the app works but looks plain, and the first
step was to establish *what* is actually in it and *what each thing is called*, so reference
designs can be hunted down per component instead of redesigning by vibe.

**Deliverable:** a published archival artifact cataloguing 47 components across the former 7 pages, each with
a screenshot, the standard industry name for the pattern, aliases it's filed under elsewhere,
and search phrases for finding references —
https://claude.ai/code/artifact/6276bc0d-9679-4974-99ca-9a413bcb5fd1

Screenshots were captured with Playwright driving Edge; the Browser pane's own screenshot tool
was unusable in that session — see [[Screenshotting-The-App]] for the recipe and the selector
traps.

## What the inventory found

The pages inventoried (`Page` union at the top of `frontend/src/App.tsx`) were Forecast,
Optimization, Upload Data, Data History, Dataset, and Settings. The archived artifact includes
the former Model Performance page; it was removed on 2026-08-12 — see
[[Model-Performance-Removal]]. **Lead Management** was added on 2026-08-20
([[Lead-Management-Page]]) and is not in the counts below; it reuses the Dataset board wholesale
and adds one new pattern of its own, the clickable stage card that doubles as a filter toggle.

Component load is very unevenly distributed:

- **Forecast** and **Dataset** carry most of it — 17 and 14 distinct patterns respectively.
- **Upload Data** (2) and **Settings** (2) are nearly bare.
- The single most reused pattern is the **popover**: campaign picker, date range, budget
  scenario, change log, filter, sort, columns, row height, and the in-cell status/quality
  selects are all the same family with inconsistent trigger and menu styling. That's the
  highest-leverage thing to unify in a redesign.
- Charts are all Recharts and all share one visual language already (amber series, teal actual,
  dashed reference lines) — they're the *least* in need of work.
- The Dataset board (toolbar, grid, filter builder, bulk bar, pagination) is effectively a
  small Airtable/Monday clone and is the densest single surface.

**Outcome:** the redesign this inventory was built for shipped 2026-08-12 — see
[[Dual-Theme-Redesign]]. The "unify the popover" call below was acted on via the token
layer rather than by rewriting each popover.

## Known gaps in the catalogue

Not captured, because they require a live action or transient state: the upload preview and
confirm bar (needs a file chosen), toast and error banners, the retraining spinner, and chart
hover tooltips.

The screenshots in the published artifact are the **pre-redesign** dark-only state and are
kept deliberately as the "before" record — they no longer match the running app.
