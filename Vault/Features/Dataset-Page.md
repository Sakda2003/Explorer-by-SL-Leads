# Dataset Page

Added 2026-08-05. New nav entry after Data History
(`frontend/src/App.tsx`, `Page` union + `nav` array + the page-render ternary in
`App()`), backed by three new endpoints in `backend/app.py` / `backend/core.py`. The
ask was "before trusting the forecast, let me see the data, the variables, and the
correlations" — this page is that audit surface, separate from Data History (which
only shows upload metadata, not content).

**Was portfolio-wide only by design; scoping added 2026-08-06 on request.** The
original reasoning (narrower scopes are too thin a sample for a correlation matrix
to mean anything, same as [[OLS-In-Forecast-Selection]]/[[Forecast-Page-OLS-Panel]])
still holds — this didn't change the underlying statistics, it just surfaces the
existing thin-scope behavior to the user instead of hiding it. A campaign
dropdown + Ad Set ID search bar (`.dataset-scope-bar`, `App.tsx`), visually matching
the Forecast page's picker but with its own local state (not the shared component —
Forecast's version carries tracking-date and lookup logic Dataset doesn't need),
sits above the first correlation matrix. Selecting a scope re-fetches
`/api/dataset/correlation`, `/api/ols-summary`, and `/api/dataset/rows` with
`ad_set_id`/`campaign_id` query params (ad set wins if both are set, same
convention as the Forecast page); the Data inventory section (top of the page)
stays unscoped, since portfolio-wide table row counts don't have a "narrow" reading.
`get_dataset_correlation()` (`core.py`) gained the same two optional params,
delegating to the already-scope-aware `_load_scope_feature_rows()` that
`get_ols_model_summaries()` already used — no new statistics code, just a second
caller. `get_dataset_rows()` already took these params and was already unused by
the frontend; this is its first wiring. A scoped fit can be a handful of
observations (an ad set's own history), so treat a narrow-scope correlation value
with the same caution as a narrow-scope OLS coefficient — it's real, computed the
same way, just from far less data than the portfolio fit.

**Correlation and Correlation-expanded merged into one toggled section, 2026-08-06.**
Was two always-visible sections stacked on the page; now one `dataset-section` with a
`correlationView: 'declared' | 'expanded'` state and a `.dataset-tabs` pair
("Declared"/"Expanded"), same pill-toggle pattern already used for the OLS
Spend-only/Multivariate switch just below it. Only the active table renders in the
DOM (verified `document.querySelectorAll('.dataset-correlation-table').length === 1`
after toggling either way) -- not a CSS show/hide, an actual conditional render, so
the 22-column "expanded" table's `calc()` column-width logic never has to coexist
with the 10-row "declared" table's fixed widths. The section header, tab pair, and
-1/0/+1 legend are grouped in one `.dataset-correlation-head-controls` flex wrapper
so they sit together at the header's right edge instead of spreading across it
under `.dataset-section-head`'s `justify-content: space-between` with a 3rd child.

Verified 2026-08-06 by dispatching real DOM events against the running dev server
(`leadlens-frontend`) rather than the harness's coordinate-based click, which
wasn't registering on this page during this session — `element.click()` /
`dispatchEvent(new MouseEvent('click', {bubbles:true}))` route through React's
delegated listener identically to a trusted click and were used to confirm: the
dropdown opens and lists real campaigns, selecting one fires
`?campaign_id=...` and updates the correlation matrix and the "Variable
dictionary" scope label, the Ad Set ID search resolves an exact ID and
syncs the campaign dropdown to its parent, and the clear button resets everything
back to portfolio-wide.

**Reused rather than rebuilt:**
- `_load_scope_feature_rows()` (`core.py`, refactored out of `get_ols_model_summaries`)
  builds the portfolio's daily feature rows once; both the OLS fit and the new
  correlation matrix call it. Verified behavior-preserving by diffing
  `get_ols_model_summaries()` output byte-for-byte before/after the extraction, at
  portfolio, campaign, and ad-set scope.
- The "variable importance" section is the existing `<OlsResultCards>` component, shared
  with the Forecast page and fed by `GET /api/ols-summary` called with no params.
- `DECLARED_VARIABLES` / `_feature_label()` / `_declared_variable_coverage()` back the
  variable dictionary table. Since the Model Performance page was removed on 2026-08-12,
  this is the canonical portfolio-wide diagnostic presentation.

**New backend surface** (`core.py`, all portfolio-scope, all read-only):
- `get_dataset_correlation()` → `GET /api/dataset/correlation` — correlation matrix
  over the declared-variable feature columns (dummy-expanded, ~20-22 columns
  depending on which vary), constant columns dropped since a zero-variance column's
  correlation is undefined, not a real 0.
- `get_dataset_overview()` → `GET /api/dataset/overview` — row counts/date
  ranges/spend totals for `lead_events` and `daily_ad_performance`, plus a
  `change_events` count that surfaces the confirmed-vs-inferred gap as a visible
  warning when it's 0 (which it is, as of this writing — variables 9/10 are 100%
  inferred proxies, not confirmed facts; see [[Change-Log-Importer]]).
- `get_dataset_rows()` / `DATASET_ROW_TABLES` → `GET /api/dataset/rows?table=leads|
  ad_performance` — paginated raw-row browser. `table` is resolved through a small
  allowlist dict (never interpolated from the request), so it can't become a SQL
  injection vector no matter what string is passed.

**Raw browser covers only `lead_events` + `daily_ad_performance`** — the two tables
that actually drive the forecast — not `change_events` (empty) or `raw_uploads`
(already visible on Data History).

**Third row-browser tab, "Combined export", added 2026-08-06.** Same
`daily_ad_performance` rows as "Ad performance", just in the column order/naming of
the raw Combined-Ad-Set-Dataset export the ad-performance importer accepts
(`Dataset/Datsaa/Dataset Template/Partial Dataset/`), for checking an upload against
what actually landed without opening the spreadsheet. New
`DATASET_ROW_TABLES["ad_performance_export"]` entry in `core.py` (same `table:
"daily_ad_performance"`, a different `columns` list/order, including
`cost_per_messaging_conversation_started`, which the "ad_performance" entry never
exposed) — no new query logic, just a second named view over the same table, same
pattern as the two correlation matrices being two views over one
`/api/dataset/correlation` response. **"Ad ID" and "Fb Ad Title" always render "-".**
They're real columns in the source export, but `daily_ad_performance` is ad-*set*-day
grain, and the importer's ad-grain rollup (`_rollup_ad_rows_to_ad_sets`) explicitly
sets `aggregated["Ad ID"] = ""` when several per-ad rows get summed into one
ad-set-day row — see [[Leadlens-Ad-Export-Grain-And-Budget]]. Showing "-" is faithful
to what's actually stored; inventing a representative ad ID would reintroduce the
exact "keep one arbitrary ad row" bug that rollup was built to fix. No backend column
exists for these two, deliberately — not a rendering gap to fix later.

**Frontend notes:** unlike the Forecast page's localized surface treatment (hard-scoped dark
surfaces, per [[Stack-and-Build]]), the Dataset page uses the theme CSS variables
(`var(--text)`, `var(--surface)`, etc.) since nothing about it needs to stay dark
regardless of theme. The correlation heatmap interpolates cell background from
`--yellow-strong` (positive) / `--danger` (negative) via `color-mix`, matching the
same warm/cold convention already used elsewhere (`.budget-impact.down`,
`.tracking-difference.over`) rather than inventing a third color language.

Verified live 2026-08-05 via the `leadlens-verify` preview server: all five sections
render with real numbers, the raw-row tabs and pagination work, and the importance
cards are the canonical portfolio-wide regression diagnostics and stay aligned with the
compact Forecast OLS view through the same endpoint and component.

**Data inventory section and Status/Signif. columns removed, 2026-08-06**, per
feedback. The "Data inventory / Tables currently imported" section (row counts per
table, expandable to date range + spend) and the "Status" ("Target"/"In model"/etc
pill) + "Signif." (Yes/No) columns on the variable dictionary table were both cut —
the variable dictionary now shows only #/Variable/What it means. `GET
/api/dataset/overview` is no longer called from this page (dropped from the
`Promise.all` alongside correlation/ols-summary); the backend endpoint itself is
untouched and still used elsewhere if needed. `overview`/`setOverview`,
`invOpen`/`setInvOpen` state, and the now-dead `DATASET_DECLARED_STATUS_COPY` map
were removed as dead code. Note: page order text above ("Data inventory ->
Variable dictionary -> ...") predates this and Data inventory no longer exists —
current order is Variable dictionary -> OLS cards -> scope filter bar ->
Correlation -> Raw data.

**Leads and Cost Per Message columns fixed in the raw-row browser, 2026-08-06.**
Both showed "-" for essentially every row on the "Ad performance"/"Combined export"
tabs, because `daily_ad_performance.leads` and `.cost_per_messaging_conversation_started`
are almost always NULL as imported from Meta's export (verified: 0/879 rows had
`leads` populated at the time of this fix). `get_dataset_rows()`'s two ad-performance
table specs (`core.py`) now `LEFT JOIN daily_ad_set_aggregates a ON a.aggregate_date =
p.day AND a.utm_ad_set_id = p.ad_set_id` (1:1 on that pair's primary key, so row count
is unaffected) and read `a.lead_count AS leads` instead of `p.leads` — this is the same
CRM-attributed count [[Ad-Decision-Engine]] already trusts over Meta's own column
(Meta's reads ~$11 CPL vs ~$1.28 actual: attribution-broken, not just sparse).
`cost_per_messaging_conversation_started` is now `COALESCE(p.cost_per_messaging_
conversation_started, spend / messaging_conversations_started)` — computed from the
two inputs that *are* always present when the stored value is missing, not a new
data source. `get_dataset_rows()` gained an optional `join` key in `DATASET_ROW_TABLES`
to support this (the COUNT query intentionally skips the join since it can't change
row count); no other table spec uses it yet.

## Layout: two CSS traps this page already hit

Fixed 2026-08-05, same day, after the page rendered ~2100px wide inside a 1265px
column and cut every section off at the right edge. Both causes are non-obvious and
will come back if the rules are "cleaned up":

1. **Grid items default to `min-width: auto`** and refuse to shrink below their
   content. `.dataset-page` is a grid, so the intrinsically-wide correlation table
   widened the single column and dragged the inventory cards, OLS cards, and row
   table off-screen with it — and the inner `overflow-x: auto` wrappers could never
   scroll, being already as wide as their content. Fixed with
   `grid-template-columns: minmax(0, 1fr)` **plus** `.dataset-page > * { min-width: 0 }`.
   Note `.model-gov-ols` / `.model-gov-ols-card` were already correct on this point
   (`minmax(0,1fr)`, `min-width:0`); they were only stretched by the parent, so the
   one parent fix repaired three sections at once.
2. **`table-layout: fixed` is load-bearing on the correlation table.** Under the
   default auto layout, the longest unbreakable header word ("recency",
   "impressions") sets a floor that declared column widths cannot go below — the
   matrix stayed pinned at ~1350px through several rounds of shrinking `width` on
   `th`, which simply had no effect. Only `table-layout: fixed` (with
   `overflow-wrap: anywhere` so long words break) made the widths authoritative.

Result: the full 22x22 matrix fits with no scrolling at a 1680px window, and at
1280px it scrolls inside its own box with row/column headers pinned via
`position: sticky` (the corner cell needs both offsets and the highest z-index, or a
diagonal scroll overlaps it). If the declared-variable list ever grows, re-check both
widths — the fit is exact, not generous.

## Redesign: from bordered cards to a flowing column (2026-08-05)

Reworked the same day, from a design export (`Dataset page redesign.zip`) that rebuilt
the page as a Design Component prototype first, then translated it back to this
codebase's conventions. Backend untouched — same three endpoints above, same
`<OlsResultCards>` reuse, same declared-variable/correlation data. Only the
presentation and the amount of standing text changed:

- **No boxed cards.** Every section (`.dataset-section`) is now separated by a thin
  `border-top` rule instead of `border` + `border-radius` + `background`. The
  correlation matrix keeps its border grid since there the border is load-bearing
  (it's a table), and the inventory items keep a vertical divider between them instead
  of individual card frames.
- **More interaction, less standing text:** inventory items collapse to label/count/
  one-line description until clicked; variable-dictionary rows 9/10 (the two inferred
  change-type variables, whose notes run long) truncate with a "(more)" toggle; the
  raw-row table defaults to 4 core columns behind "Show all columns".
- The redesign export's first pass had defaulted correlation to a client-computed
  "top 6 relationships" list behind a "Top relationships / Full matrix" tab, to avoid
  showing all 22 columns up front. Removed 2026-08-05 per feedback — correlation now
  always shows the full matrix, no tab, no client-side pair-ranking. If this comes
  back, the sorted-by-`|value|` derivation lived in `topPairs` inside `DatasetPage()`
  and can be resurrected from git history rather than re-derived from scratch.
- The inner `.dataset-correlation-scroll` box originally capped at `max-height: 78vh`
  with `overflow: auto` on both axes, which forced scrolling the box *and* the page to
  see the whole 22x22 matrix. Fixed 2026-08-05: removed the vertical cap and switched
  to `overflow-x: auto` only, so the full matrix renders at its natural height and only
  the page scrolls. Sticky row/column headers (`position: sticky`) still hold correctly
  against the page scroll now instead of the removed inner one.
- **Explanatory text stripped down 2026-08-05**, per feedback: removed the heading
  paragraph, the "Change log is empty" warning banner entirely (not just collapsed),
  the inventory table-name `<code>` tag and description paragraph, and both
  correlation matrices' caption/hint text. Page now leans on the section headers,
  column labels, and hover tooltips to carry meaning instead of standing prose.
  `warningOpen` state and the now-dead `.dataset-warning*`/`.dataset-heading p`/
  `.dataset-correlation-hint` CSS were removed along with it — if the change-log-empty
  warning needs to come back, `overview.change_events` (from `GET /api/dataset/overview`)
  still carries `{total, note}`, nothing on the backend changed.
- **Header typography/sizing fixed 2026-08-05** (via `/impeccable`), after headers were
  rendering as cramped, forced-uppercase, mid-word-wrapped monospace (e.g.
  "DAYS_SINCE_ADSET_S / TARTED"). Root causes, both pre-existing app-wide leaks this
  page never opted into:
  1. A global typography rule, `code, kbd, .nav-label, .eyebrow, th, … { font-family:
     var(--font-mono) }` (styles.css ~L3354), forces every `<th>` in the app to
     monospace. A bare `th { font-size: 10px }` (~L3389) and an earlier `th { font-size:
     9.5px; font-weight:700; text-transform:uppercase; height:39px }` (~L1694) added the
     rest. None of these can be beaten by styling the ancestor `<table>` -- a directly
     targeted element rule always wins over an inherited value, regardless of the
     ancestor selector's specificity -- so `.dataset-correlation-table th, td` now sets
     `font-family`/`font-size` directly, and `.dataset-correlation-table th` explicitly
     resets `text-transform`, `letter-spacing`, `height`, `overflow-wrap` back to normal.
  2. A bare `table { width: 100%; min-width: 1050px }` (~L1693) stretched this table
     past the sum of its own declared column widths, and `table-layout: fixed` then
     inflated every column proportionally to fill the leftover space -- which is why the
     10-var and 22-feature matrices previously rendered their identically-styled
     156px row-header column at two different actual widths (195px vs 156px). Fixed
     with an explicit `width: auto; min-width: 0` on `.dataset-correlation-table`.
  Follow-up same day: the expanded 22-feature matrix (`.is-dense` class on its
  `<table>`) was reworked to fit entirely on screen with no horizontal scroll at all,
  since 22 columns at a legible fixed px width simply can't fit any reasonable
  viewport. Three things had to change together:
  1. Data-column width is computed in `App.tsx` as an inline
     `calc((100% - 130px) / N)` (N = `correlation.variables.length`) instead of a
     fixed px or `auto` -- `auto` columns under table-layout:fixed size off each
     label's content width instead of dividing evenly (confirmed empirically: columns
     came out 30-105px, proportional to each un-rotated label's length).
  2. That `calc(%...)` only resolves against a *definite* table width -- with the
     table's own `width` at `auto` (only `min-width: 100%` as a floor, from the
     stretch fix above), percentages on child cells silently fell back to `auto` too.
     `.dataset-correlation-table` now sets `width: 100%` as well as `min-width: 100%`;
     the two rules serve genuinely different mechanisms (min-width forces the
     px-pinned 10-variable matrix to stretch; width makes the dense matrix's
     percentages resolve) and neither can be dropped.
  3. Column headers rotate a full **-90deg**, not a diagonal tilt. A diagonal rotation
     (first tried at -38deg) still projects a horizontal footprint that grows with
     label length -- "msg template change" at -38deg still overflowed ~85px past the
     table edge. A full 90deg turn makes the rendered footprint exactly one
     line-height wide (~13px) regardless of how long the label is; the label instead
     grows the header row's height (150px), which was free to spend. Verified via
     `scrollWidth === clientWidth` on the scroll container (no scrollbar) after each
     iteration, since the harness's browser pane couldn't screenshot this session.
  Follow-up same day (earlier): after `width: auto` fixed the two matrices' column-width
  inconsistency, the 10-variable matrix (narrower than its section) sat stranded at the
  left with dead space to the right. Root cause: under `table-layout: fixed`, once
  every column has an explicit width (min/max-width via thead th, as ours all do),
  there are no auto-width columns left to absorb slack, so a plain `width: 100%` is a
  no-op -- the table just renders at the sum of its column widths. `min-width` is a
  hard floor the renderer still has to satisfy, so `min-width: 100%` (not `width: 100%`)
  is what actually forces the narrower matrix to stretch and fill; the wider,
  22-feature matrix already exceeds that floor so it's unaffected and still scrolls.
  Also: the 10-variable matrix's labels were the raw backend `DECLARED_VARIABLES` names
  (`Holiday_Proximity`, `days_since_adset_started`, mixed casing) -- these are
  identifiers, not display copy. Added `DATASET_DECLARED_SHORT_LABEL` (a 10-entry map
  in `App.tsx`) so the matrix reads "Holiday proximity" / "Ad set age" etc., matching
  the wording `_feature_label()` already produces for the expanded matrix. Column width
  went 52px to 74px, row-header 116px to 156px, font-size 9.5px to 11.5px sans (was
  inherited mono) -- verified via computed-style checks (both matrices' row headers now
  measure identically) since the harness's browser pane couldn't screenshot this
  session.
- **Second, collapsed 10x10 matrix added above the feature-level one.** The original
  matrix is per-*feature*, not per-*declared-variable* — some of the 10 (Holiday_Proximity,
  Days of the week, the two change-type variables) expand into several dummy columns, so
  that matrix is really ~22x22. Added 2026-08-05 a second matrix, "How the ten declared
  variables move together", collapsed to exactly the 10 rows/columns in the variable
  dictionary above it. Derived entirely client-side (`declaredCorrelation` in
  `DatasetPage()`) by grouping `correlation.variables` on `variable_number` and picking,
  for each pair of groups, the **strongest** (max `|r|`, sign kept) sub-feature
  relationship rather than an average — an average would wash out a single dominant
  pairing (e.g. `ad_set_change_type` vs `ad_set_change_recency` is dominated by one
  sub-feature pair, not a blend of all of them). No backend change; both matrices read
  the same `/api/dataset/correlation` response, so they can't drift from each other. The
  original 22-column matrix stays below it, relabeled "Correlation, expanded" for the
  full sub-feature detail.
- **Cold/hot correlation colors, not gold/red.** The original heatmap reused
  `--yellow-strong` (positive) / `--danger` (negative) — the same warm-positive/
  alert-negative pair used elsewhere in the app (`.budget-impact.down`,
  `.tracking-difference.over`). Replaced 2026-08-05 with a dedicated diverging pair,
  `--corr-cold` (blue, negative) / `--corr-hot` (red, positive), scoped to this matrix
  only via `correlationCellStyle()` — doesn't touch the warm/cold convention used
  elsewhere. Cells mix toward `--surface` (not `transparent`) so near-zero values stay
  legible on both themes instead of just fading to see-through. A small gradient
  legend (`-1 / 0 / +1`, `.dataset-correlation-legend`) sits next to the section
  heading, built from the same two `color-mix` stops as the cells so it can't drift
  out of sync with what the cells actually render.
- **Variable importance still reuses `<OlsResultCards>`** — the redesign's export had
  flagged this as an open decision (drop the shared component for a one-off render, or
  extend it). Extended it instead: `OlsResultCards` takes an optional `view` prop
  (`'univariate' | 'multivariate'`, shows one fit at a time when set) and
  `collapseTerms` (shows the top 6 coefficient rows behind "Show all N terms"). Both
  default to the old behavior, so Forecast is unaffected. The
  boxed-card look is stripped back to flat/flowing just for this page via
  `.dataset-ols .model-gov-ols-card` overrides, not by forking the component — so the
  two remaining pages still cannot show conflicting numbers for the same regression.
- **Simplified to stats-only, both fits side by side, 2026-08-06.** Was a
  Spend-only/Multivariate tab toggle (`view={olsView}`) showing one full card
  (fit stats + coefficient term table + "Variables: ..." footnote) at a time, per
  feedback that the term table was noise for this page's purpose. Now
  `<OlsResultCards ols={ols} coefficients={false} .../>` with no `view` — the
  existing `coefficients` prop (already used elsewhere, e.g. the Forecast page's
  `coefficients={false}` sidebar card) hides the term table, the "N vars" badge, and
  the features footnote, leaving just the R2/Adj R2/F p-value/RMSE row; omitting
  `view` makes both Spend-only and Multivariate cards render together instead of
  toggled. `olsView` state and the tab buttons were removed as dead code. No backend
  or `OlsResultCards` component change — both knobs already existed for other
  callers, this just picked a different combination of them.
- **Section header ("Variable importance" / "Same portfolio OLS fit shown on Model
  Performance") removed same day**, per feedback — the two OLS cards render with no
  `dataset-section-head` of their own.
- **Reordered same day**: the OLS cards section moved from after Correlation to
  between Variable dictionary and the scope filter bar. Page order is now Data
  inventory -> Variable dictionary -> OLS cards -> scope filter bar -> Correlation
  -> Raw data. Pure JSX reorder, no state/logic change — the OLS cards still read
  the same portfolio-or-scoped `ols` state as everything below them, so moving them
  above the filter bar doesn't change what they show, just where the cards sit
  relative to it on the page.
- **Fixed a misalignment in the two-card row, same day.** `.dataset-ols .model-gov-ols-card
  + .model-gov-ols-card` had `margin-top`/`padding-top`/`border-top` -- a divider meant for
  cards *stacked* vertically -- while the base `.model-gov-ols` (`styles.css`) lays the two
  cards out as a 2-column grid side by side. Result: the Multivariate card visibly sat lower
  than the Spend-only card, offset by exactly that top spacing. `.dataset-ols.model-gov-ols
  { gap: 0 }` had also zeroed the grid's horizontal gap, so the two columns had no breathing
  room between them either. Fixed by dropping the `gap: 0` override (base 20px grid gap
  applies again) and switching the second card's divider from top-anchored to
  `border-left` + `padding-left: 20px` -- a divider between side-by-side columns, not
  stacked rows. Verified both card headers now share the same `getBoundingClientRect().top`.
- Verified live via a temporary `leadlens-frontend` Vite dev entry added to
  `.claude/launch.json` (proxies `/api` to the `leadlens` backend on :8000): all tabs
  (top/full correlation, spend-only/multivariate OLS, leads/ad-performance rows,
  show-all-columns, show-all-terms, inventory/variable-note expand) work against real
  data, `tsc --noEmit` clean, no console errors.

**"Calculation" section added below the correlation matrix, 2026-08-06**, per request
to show how the matrix is actually computed, not just the numbers — then simplified
same day, per feedback, from a text-paragraph + wide data table down to one compact
visual card (`.dataset-formula-card`). New `dataset-section` between Correlation and
Raw data, gated on `declaredCorrelation` (same guard the matrix itself uses). No new
backend endpoint — everything below is derived client-side from data the page already
fetched:
1. The Pearson formula as a stacked fraction (`r = cov(x,y) / σx·σy`), CSS-built
   (`.dataset-formula-frac`, border-top rule for the division line), not an image or
   a math-rendering library.
2. Three stat chips next to it: `correlation.sample_size` ("N days"),
   `correlation.date_start`/`date_end`, and the current scope label (ad set/campaign/
   portfolio, same three-way check used elsewhere on this page) — self-updating when
   the scope filter above changes, same as the matrix itself.
3. One small chip per declared variable (`.dataset-formula-chip`, flex-wrap grid) —
   short label + the literal feature column(s) that variable's collapsed correlation
   cell was built from (`declaredCorrelation.variables[i].indices` mapped back through
   `correlation.variables[index].label`; can be a subset of that variable's full
   feature list when a sub-feature had zero variance and got dropped, e.g. only
   "during holiday" for Holiday proximity when the other three holiday-window columns
   were flat this window). Truncates with `text-overflow: ellipsis`, full list in the
   `title` tooltip on hover.
The original version's per-variable "What feeds it" prose column (duplicating the
Variable dictionary section above) and the two paragraphs of explanatory text were cut
entirely, not just reworded — the request was explicitly for less text, more visual.

**"Ad set change" recorder added to the scope bar, 2026-08-06.** Same popover the
Forecast page already had (record a budget/targeting/placement/bid change, an ad-level
change, or an ad set's true start date) — see
[[Change-Event-UI-Recorder]] for the extraction into a standalone
`ChangeEventButton({ adSetId })` component that made this possible.
`<ChangeEventButton adSetId={selectedAdSetId} />` sits at the end of
`.dataset-scope-bar`, after the Ad Set ID lookup form, so it's always scoped to
whatever ad set the Dataset page's own campaign/ad-set filter currently has selected —
it shows "Select an ad set to record its changes" the same way Forecast's copy does
when nothing is scoped.

**Declared variables 4/6/7/9/10 added to the "show all columns" view of the
"Ad performance" and "Combined export" raw-row tabs, 2026-08-06, then blanked same
day.** First wired up with per-row detector inference (unlike
[[Ad-Decision-Engine]]'s Optimization table's one-anchor-day value per ad set, this
browser is row-per-(ad_set_id, day) so every row got its own value as of *that* day),
via `_days_since_start_values` / `_ad_change_features` / `_ad_set_change_features`
batched once per ad set. Reported wrong by the user the same day. `_attach_declared_
variables()` in `core.py` (still called from `get_dataset_rows()` for `table in
_DECLARED_VAR_TABLES = {"ad_performance", "ad_performance_export"}`) now just sets
all five `_DECLARED_VAR_KEYS` to `None` on every row — columns stay visible (pending
real recorded data), values are blank. The detector-based version is gone from the
code, not just disabled; if a better detector shows up later this needs rebuilding,
not un-commenting. Frontend needed no change — its render fallbacks already print
`-`/`—` for a missing value. Not on the "Leads" tab (no ad-set/day grain there).

**Un-blanked 2026-08-06 — the stub outlived its reason.** The `= None` loop above was
the right call while the only available source was a wrong detector, but it was never
revisited once the "Ad set change" popover started producing *confirmed* data. Result:
recorded changes reached the OLS fit and the correlation matrix but never the raw
table, which kept printing `—` and looked like the recording had failed.
`_attach_declared_variables()` now reads the same two confirmed sources the model reads
(`_confirmed_ad_set_starts()`, `_recorded_change_events()`), via a new one-day helper
`_change_state_as_of()` that applies `_resolve_change_state`'s carry-forward rule to a
single row. Events are hoisted per distinct ad set before the row loop — `_recorded_
change_events` is `lru_cache(maxsize=64)` and a 500-row page spanning >~32 ad sets
would otherwise evict its own entries mid-loop. Frontend unchanged again; the columns
and their `?? '-'` fallbacks were already in `DATASET_ROW_COLUMNS`.

**Unrecorded stays `None` here, not the model's `0`.** The fit needs a numeric column
and reads "nothing recorded" as a zero-variance zero (which `get_dataset_correlation`
then drops); this table is an inspection surface, where a real "changed today"
(recency 0) has to stay distinguishable from "never recorded". Days *before* a
confirmed start date are `None` for the same reason — `_days_since_start_values`
clips that age to 0 for the model, but a negative or zero age on the page would read
as a data error rather than "not launched yet". This is a deliberate divergence
between table and model, not drift; don't "fix" it by mirroring the zeros.

**The rows fetch was missing its refresh key.** `DatasetPage`'s `/dataset/rows` effect
was keyed `[rowsTable, rowsData.offset, scopeParams]` — the same latent bug documented
in [[OLS-Declared-Ten-Variables]] for the correlation/OLS effects, in the one place
that never got patched when those were. Without `dataRefreshKey` in that array the
newly-populated columns wouldn't appear until a scope change or full reload, which
would have looked exactly like the stub was still there. Verified live 2026-08-06:
deleting a recorded start date through the popover flipped the "Ad set age" column
from 51/50/49 to `-` in place, no reload.

**"Not shown above" note added under the correlation matrix, 2026-08-07.** The declared
matrix already silently dropped constant declared variables (correct — a zero-variance
column has no defined correlation), but nothing near it said which ones were missing or
why, so a real statistical outcome kept reading as a bug (second occurrence — the first
was an out-of-window event date). `missingDeclaredVariables` (`App.tsx`) diffs
`declaredCorrelation.variables` against `ols.declared_variables` (already fetched for the
Variable dictionary section above) and renders `.dataset-correlation-missing` — number,
short label, and the backend's own `detail` string — only under the "Declared" tab. No
new backend endpoint; reuses `_declared_variable_coverage`'s existing status computation
so this note and the matrix can never disagree about what's present. See
[[OLS-Declared-Ten-Variables]] for the specific type-dummy-baseline finding that
triggered this.

**Advanced column filter bar added above the "Raw data" table, 2026-08-07**, modeled on
Monday.com's "Where [column] [is] [value]" advanced filter (a screenshot of that UI was
the reference). Sits in `.dataset-rows-controls`, right of the Leads/Ad performance/
Combined export tabs, as a `FilterBar` popover (`App.tsx`) reusing the `.selector`/
`.campaign-menu` chrome the scope-bar campaign picker already established, so it reads as
the same control family rather than a bolted-on widget.

- **Field/operator/value model, not free text.** Each table has a fixed list of
  filterable fields (`DATASET_FILTER_FIELDS` in `App.tsx`, mirrored server-side by
  `DATASET_ROW_TABLES[*]["filter_fields"]` in `core.py`) typed as `text | number | date |
  enum`. The operator dropdown is generated from the field's type
  (`FILTER_OPERATORS`): text gets contains/not contains/is/is not/is empty/is not empty;
  number gets =/≠/>/≥/</≤ plus the two empty checks; date gets is/is before/is after/is
  between/empty checks; enum (currently only Leads' Status: New/Existing) gets is/is not
  rendered as toggle chips, not a dropdown, since there are only two values. Multiple
  filter rows AND together — no OR, no groups, matching Monday's screenshot's single
  "Where" clause, not its fuller group feature (not needed for a handful of fields on one
  table).
- **Backend allowlist, same pattern as `campaign_id`/`ad_set_id` scoping already used
  by this endpoint.** `get_dataset_rows()` gained a `filters: list[dict] | None` param;
  each `{field, operator, value}` row's `field` is looked up in the table's
  `filter_fields` dict (never interpolated from the request) to get the real SQL column
  and type, and `_build_filter_clause()` validates the operator against
  `_FILTER_OPERATORS_BY_TYPE` for that type before building parameterized SQL — same
  can't-become-injection guarantee as `DATASET_ROW_TABLES`'s table-name allowlist,
  extended to filter fields. `GET /api/dataset/rows` takes the filter list as one
  JSON-encoded `filters` query param (`json.loads`, 400 on bad JSON/non-array) rather than
  repeated `?filters=`, since each row's three parts need to stay grouped.
- **"ad_performance" and "ad_performance_export" share one field set**
  (`_AD_PERFORMANCE_FILTER_FIELDS` in `core.py`) since both read the same
  `daily_ad_performance p` table under the same alias — a filter means the same thing in
  either tab, same reasoning as [[Leadlens-Ad-Export-Grain-And-Budget]]'s "same rows, two
  views" for the columns themselves.
- **Declared-variable columns (#4/#6/#7/#9/#10 — age, change recency/type) are not
  filterable.** `_attach_declared_variables()` computes them in Python *after* the SQL
  page is fetched (see the "was missing its refresh key" note above), so they can't
  participate in a `WHERE` clause without breaking `LIMIT`/`OFFSET` pagination — filtering
  is intentionally scoped to real SQL columns only.
- Switching the Leads/Ad performance/Combined export tab clears `rowFilters` (field keys
  are table-specific, so a leftover filter would either silently no-op or, worse, look
  like it applied to a field the new tab doesn't have). Completing a filter row also
  resets pagination to page 1, same as the existing scope-filter reset just below it in
  the effect list. A filter row only ships to the backend once it has a usable value
  (`isFilterRowComplete`) — a half-typed text/number box or an empty "contains" would
  otherwise round-trip as either a no-op or, worse, a `LIKE '%%'` matching every row.
- Verified live 2026-08-07 via a `leadlens-frontend` preview: Status "is New" narrowed
  Leads from 3,023 to exactly 2,202 (matches the dashboard's own New-leads count);
  switching tabs clears the filter and its query param; Ad performance's Spend "> 5"
  round-tripped as `filters=[{"field":"amount_spent_usd","operator":"gt","value":"5"}]`
  and returned only rows with spend above $5; `tsc --noEmit` clean.

**Explicit "Apply" button added same day, per feedback** — filters initially applied live
on every edit (each field/operator/value change immediately refetched), which the user
asked to change to an explicit commit step. `DatasetPage` now holds two filter states:
`rowFilters` (the draft the popover edits — field/operator/value changes, add/remove row)
and `appliedRowFilters` (what the `/dataset/rows` fetch effect actually reads). `FilterBar`
gained `onApply` (copies draft → applied, wired to a `button.primary` "Apply" in a new
`.filter-menu-footer` row alongside "+ New filter") and `onClearAll` (clears both draft and
applied immediately — clearing has nothing to preview, so it stays instant, matching
Monday's own "Clear all"). The closed button's "Filter / N" badge now reads `appliedCount`
(derived from `completeRowFilters`, itself now sourced from `appliedRowFilters`) rather than
the draft's complete-row count, so it can't claim a filter is active before Apply is
clicked. Apply is `disabled` until the draft has at least one complete row
(`isFilterRowComplete`); clicking it does not close the popover, so a user can keep
refining and re-applying without reopening. Switching the Leads/Ad performance/Combined
export tab now resets both `rowFilters` and `appliedRowFilters`. Verified live: selecting
the "New" status chip fired no `/dataset/rows` request; clicking Apply fired
`filters=[{"field":"status","operator":"is","value":["New"]}]` and the badge updated to
"Filter / 1"; `tsc --noEmit` clean.

**Field/operator pickers restyled as a custom icon menu, 2026-08-07**, per feedback that the
native `<select>` dropdowns looked "boring" — reference was a macOS-style rounded context
menu (icon + label rows, checkmark on the current value). New `MenuSelect` component
(`App.tsx`) replaces both `<select>`s in a filter row; one icon per field *type* (`Pencil`
text, `Gauge` number, `CalendarDays` date, `UserCheck` enum — `FILTER_TYPE_ICON`), no icons
on the operator menu (kept as a plain checkmarked list, matching the reference's simpler
sub-menus).

**Clipping bug caught during the same pass and fixed the same way as two prior cases in this
file** (`SingleDatePicker`'s calendar, the change-type dropdown): the first version rendered
`.menu-select-menu` `position: absolute` inside the filter row, which put it inside
`.filter-menu`'s own `overflow: auto` box (420px max-height) — a menu opened on a filter row
near the bottom of the panel got its lower options cut off by that scrollbar, not the
viewport. Fixed by portaling to `document.body` with measured `position: fixed` coordinates
(`useLayoutEffect` + a `measure()` on open/scroll/resize, flip to `opens-upward` when there's
more room above than below), the exact same pattern already used twice elsewhere in this
file. Two knock-on outside-click fixes were required, also matching prior precedent: (1)
`MenuSelect`'s own outside-click listener now also allows clicks inside `.menu-select-menu`
found via `closest()`, since a portaled menu is no longer a DOM descendant of the trigger's
wrapper ref; (2) `FilterBar`'s outer popover-close listener needed the same `closest('.menu-
select-menu')` exception, or picking a field/operator would register as a click outside the
whole "Advanced filters" panel and close it before the selection's `onChange` fired. Verified
live: the field menu opens fully visible (9 options, no clipping) regardless of where the
filter row sits in the panel, selecting "Amount" swaps the field and resets the operator/value
without closing the outer popover, Escape closes only the innermost open menu, and Apply still
fires the correct `filters=` query (`{"field":"amount_spent_usd","operator":"eq","value":"4"}`
round-tripped exactly).

**"Clear all" could go missing while a filter was still applied, fixed 2026-08-07.**
`FilterBar`'s "Clear all" button and its "No filters applied yet" empty state were both
gated on `filters.length` (the in-progress draft) rather than on what was actually applied.
Reported scenario: apply a filter, then remove its row with the row's own X *without*
clicking Apply again -- the draft goes to zero rows, but `appliedRowFilters` (and the
"Filter / N" badge) still holds the one that was applied. With draft-only gating, "Clear
all" disappeared and the empty-state copy claimed no filter was active, while the table
kept showing filtered rows -- no control left to get back to unfiltered short of switching
tabs (which happens to reset both states as a side effect) or reloading. Compounding it:
"Apply" is `disabled` on an empty draft (`draftReady = filters.some(isFilterRowComplete)`),
so it couldn't be used to push the empty draft through either. Fixed by gating "Clear all"
on `filters.length > 0 || appliedCount > 0` instead of the draft alone, and splitting the
empty-state copy on the same condition ("All filter rows removed -- click 'Clear all' to
also clear the table filter" vs the original "No filters applied to this table yet").
Verified live: apply Status "is New", remove the row via its X, "Clear all" stays visible
with the new interim copy, clicking it fires the unfiltered `/dataset/rows` request and the
"Filter / N" badge clears.

**Quick date-range picker added next to the Filter button, 2026-08-07.** Reference was a
screenshot of a preset-sidebar + two-month-calendar range picker (Today/Yesterday/This week/
Last week/This month/Last month/This year/Last year/All time + Custom, dual calendar, Cancel/
Apply). Deliberately a *separate* control from `FilterBar`'s advanced filters, not a "date"
field row inside it — the whole point was skipping the "pick a column first" step for the one
column every table has (a date). New `PresetDateRangePicker` component in `App.tsx`, portaled to
`document.body` like `SingleDatePicker` (its ~660px two-month width would otherwise get clipped
by `.dataset-rows-controls`' own layout), trigger reuses `.selector.filter-selector` chrome so it
sits as the same control family as the Filter button beside it.

- **Rides the existing filter pipeline as one more row, not a new query param.** `DatasetPage`
  holds `rowDateRange: {from, to} | null`; `allRowFilters` (`useMemo`) appends
  `{field: rowDateField, operator: 'between', value: rowDateRange}` to `completeRowFilters` when
  a range is set, where `rowDateField` is `'created_at'` for Leads and `'day'` for Ad
  performance/Combined export (both already-filterable date columns in `DATASET_FILTER_FIELDS`/
  `_AD_PERFORMANCE_FILTER_FIELDS`). No backend change — the `between` operator and `{from, to}`
  value shape already existed for FilterBar's own date fields.
  `filterQuery`/`rowFiltersKey` were switched from `completeRowFilters` to `allRowFilters` so the
  quick range and the advanced filters compose (both AND together) and both trigger the same
  refetch/pagination-reset effects.
- **Persists across Leads/Ad performance/Combined export tab switches**, unlike `rowFilters`
  (which the tab buttons still clear, since those field keys are table-specific). A date range
  isn't table-specific — only the underlying column name is, and `rowDateField` recomputes that
  automatically — so clearing it on every tab switch would have been a worse default than
  advanced filters' clear-on-switch, not the same case.
- Preset math (`quickRangeFor`) is plain `Date` arithmetic, ISO week (Monday start, matching
  `date-range-grid`'s existing `['M','T','W','T','F','S','S']` header elsewhere on this page).
  "This week"/"This month"/"This year" run start-of-period through *today*, not through the
  period's end, so a preset never implies data that hasn't happened yet.
- Verified live 2026-08-07 via `leadlens-frontend`: popover renders 10 presets + two calendars
  (84 day-cells) positioned via the same `useLayoutEffect` reposition pattern as
  `SingleDatePicker`; picking "This month" and Apply fired
  `filters=[{"field":"created_at","operator":"between","value":{"from":"2026-08-01","to":"2026-08-07"}}]`
  and the table narrowed from 3,023 to 7 rows; switching to the Ad performance tab re-fired with
  `"field":"day"` and the same dates, unprompted; picking "All time" and Apply dropped the
  `filters` param entirely. `tsc --noEmit` clean.

**Two-month calendar overlap bug, fixed 2026-08-07 — same day.** Reported live: the two
months' digits visibly interleaved ("12" + "3" reading as "123", etc.), the right calendar
bleeding into the left one instead of sitting cleanly beside it. Root cause, confirmed via
`scrollWidth`/`clientWidth` on `.date-range-grid`: `.date-cell` is a bare `<button>` that never
reset the browser's default UA padding (`1px 6px`), so each cell's real min-content width was
~40px (28px date span + 12px padding) — and `.date-range-grid`'s `grid-template-columns:
repeat(7, 1fr)` (no `minmax(0, ...)`) refuses to shrink a `1fr` track below that content
minimum. At the original single 300px popover (`DateRangePicker`/`SingleDatePicker`, what this
class was written for) there was always enough room, so the trap never fired; splitting the
same class across two side-by-side calendars in a 660px popover squeezed each month to 231px,
well under the 280px the grid demanded, and a `1fr` track that can't shrink overflows its box
instead of clipping — spilling into the neighboring flex sibling since neither
`.preset-date-month` nor `.date-range-grid` had `overflow: hidden`. Three-part fix in
`styles.css`, all to the shared classes (so the original single-month pickers get the same
robustness, not just this one): `.date-cell` now sets `padding: 0; width: 100%` instead of
inheriting UA button padding; `.date-range-grid` changed to `repeat(7, minmax(0, 1fr))` so
columns can actually shrink to fit; `.preset-date-month` gained `overflow: hidden` as a second
line of defense. Verified via `scrollWidth === clientWidth` (231 === 231, was 280 vs 231) on
both month grids and `getBoundingClientRect()` showing a clean 18px gap between the two
`.preset-date-month` boxes with no horizontal overlap; `npm run build` clean. Same class of bug
as this file's "Grid items default to `min-width: auto`" note above (Dataset page's original
layout fix) — a `1fr` grid track and a `min-width: auto` flex item are two different mechanisms
with the same failure mode: "shrink-to-fit" is not the default, and a cheap browser default
(button padding here) is exactly the kind of hidden minimum that trips it.

**"Show all columns" toggle removed, 2026-08-07 — all columns render by default now.** Per
request. `DATASET_ROW_COLUMNS` (`App.tsx`) collapsed from `{short, all}` per table to one flat
column list per table (what `all` used to be) — Leads now always shows all 9 columns, Ad
performance all 15, Combined export all 21, no truncated first-pass view behind a click.
`showAllColumns` state and the `.dataset-link-btn` toggle button were removed as dead code;
`columns` is now just `DATASET_ROW_COLUMNS[rowsTable]`. Verified live: header row for Leads
lists all 9 columns (Created/Customer/Status/Campaign/Campaign ID/Ad set ID/Ad ID/Ad title/
Amount) with no toggle button present, Combined export lists all 21; `tsc --noEmit` and
`npm run build` both clean.

**Variable dictionary moved to the bottom of the page, 2026-08-07.** Per request. Was the first
section under the page header (a wall of definitions before any actual data); pure JSX reorder
in `DatasetPage()` — the section's markup, `declaredVariables` derivation, and `varOpen`
truncation state are all untouched, it just moved past "Raw data" instead of before "OLS cards".
Page order is now OLS cards -> scope filter bar -> Correlation -> Calculation -> Raw data ->
Variable dictionary (previously Variable dictionary -> OLS cards -> ... -> Raw data). Verified
live via `.dataset-section-head` text order (`Correlation, Calculation, Raw data, Variable
dictionary`, OLS cards and the scope bar have no section head) and confirmed all 10 rows still
render at the new position; `tsc --noEmit` and `npm run build` clean.

**Raw-row sort order flipped to oldest-first, 2026-08-07.** Per request. All three
`DATASET_ROW_TABLES` specs in `core.py` (`leads`, `ad_performance`, `ad_performance_export`)
had `"order_by": "<date column> DESC"`; changed to `ASC`. `ad_performance_export` shares the
same dict entry's `order_by` as `ad_performance` (see "same rows, two views" note above), so
one change covers both tabs. No frontend change — pagination (`offset`/`limit`) and the date
filter's `between` bounds are order-agnostic, they just walk whatever order the SQL returns.
Verified via `get_dataset_rows()` directly (Leads' first 5 rows are all `2026-06-06`, was
`2026-08-01`) and live in all three tabs (Leads/Ad performance/Combined export each now open
on Jun 6, 2026, the earliest date in the dataset, instead of Aug 1).

**"Not shown above" note narrowed to the recorded-but-degenerate case only, 2026-08-07,
per feedback.** The note added earlier the same day fired for EVERY missing declared
variable, including the plain "nothing recorded for this scope yet" case -- which is the
expected default for an unscoped ad set, not news, and the user asked for it to stop
appearing when there's simply no data yet ("I already know"). `_declared_variable_
coverage()` (`core.py`) now emits a distinct `status: "flat_recorded"` only for the one
case worth interrupting for -- a change IS recorded but still constant (covers every
observed day, no baseline left) -- while plain "nothing recorded" stays `status: "flat"`
as before. `missingDeclaredVariables` (`App.tsx`) filters on `status === 'flat_recorded'`
instead of "any status besides target that's absent from the matrix". Verified: an ad set
with zero recorded events/start date now shows no box at all (previously listed all 5 of
#4/#6/#7/#9/#10); the synthetic "recorded on every day" case still correctly yields
`flat_recorded` and would still show the box. See [[OLS-Declared-Ten-Variables]] for the
original note this narrows.

## Raw data rebuilt as a Monday.com-style board, 2026-08-08

The "Raw data" section went from a plain paginated `<table>` to a full board surface. Same
three tabs, same endpoint, same filter bar and quick date picker — but the table itself now
sorts, searches, hides/resizes columns, selects rows, bulk-acts, and (on Leads) edits in
place. Structure copied from Monday.com; material is entirely this app's own tokens. See
[[Lead-Drilldown-Inline-Edit]] for the sibling board on the Forecast page, whose
`LeadEditableCell` and status-pill `MenuSelect` this reuses verbatim.

**Backend gained sorting and search** (`core.py`, `app.py`) — both server-side, deliberately.
The board pages 50 rows at a time, so a client-side sort or search would answer a different
question than the one asked ("the largest spend" vs "the largest spend *on page 3*").
- `get_dataset_rows()` took `sort`, `direction`, `search`. `sort` is a key into a new
  per-table `sort_fields` allowlist, built by `_sort_fields_from(filter_fields, **extra)` —
  everything filterable is sortable, plus the joined/computed columns that have no single
  base-table column to filter on (`a.lead_count AS leads`, the COALESCE'd cost-per-message,
  `ad_set_budget_type`). SQLite will happily `ORDER BY` a SELECT alias.
- Same injection guarantee as the existing table/filter allowlists: the SQL column is always
  the hardcoded spec, `direction` collapses to the literal `ASC`/`DESC`, and only `search`'s
  value is bound. An **unknown sort key falls back to the table's default order rather than
  raising** — a stale column key from a previously-open tab shouldn't blank the table.
- `ORDER BY {col} IS NULL, {col} {dir}` keeps blanks at the bottom in *both* directions.
  SQLite ranks NULL below everything, so a plain `ASC` floats every empty row to the top,
  which is the opposite of what a spreadsheet user expects on a sparse column.
- Search sweeps a per-table `search_columns` list — text-ish columns only. LIKE against a
  REAL/INTEGER column matches its string rendering, so searching "3" would hit every row
  whose spend merely contains a 3. `LOWER()` on both sides (matching the text filters' existing
  idiom) because SQLite's LIKE only folds case for ASCII and many customer names are Khmer.
  New `_escape_like()` neutralises `%`/`_` so a searched wildcard matches itself; the older
  per-field filter operators predate this and still splice raw terms.

**Frontend board** (`App.tsx`, `styles.css`, all new `.board-*` classes):
- Toolbar: expanding search (debounced 350ms), the existing date picker + FilterBar, then
  Sort / Columns / row-height popovers. All three ride `.selector`, so they read as the same
  control family as the campaign picker and filter button already beside them.
- Headers: click cycles asc → desc → unsorted (a spreadsheet's three-state cycle, so a
  mis-click is one more click from undone), and the right edge drags to resize. Columns the
  backend can't sort render an inert header with a tooltip explaining why — the declared
  variables (#4/#6/#7/#9/#10) are attached in Python *after* the query by
  `_attach_declared_variables`, so no `ORDER BY` can reach them, and the export tab's
  "Ad ID"/"Fb Ad Title" are hardcoded `-` placeholders with no column behind them.
  `DATASET_SORT_FIELDS` in `App.tsx` mirrors the backend allowlist.
- Frozen header (`z-index: 3`) and frozen checkbox column (`z-index: 2`); the select-all cell
  where they cross is `4`, so the header still wins.
- Row selection with a floating batch bar (Export CSV always; Delete only on Leads, the one
  table with a row-level write endpoint). Bulk delete runs **sequentially, not `Promise.all`** —
  each delete rewrites the same aggregate tables, and concurrent writes against SQLite trade a
  tidy loop for lock contention.
- CSV export writes the *visible* columns in their current order with each column's own
  renderer applied, so the file reads the way the board does, not the way the DB does.
- Inline editing on Leads reuses `LeadEditableCell` + the status-pill `MenuSelect`, with the
  same optimistic single-field PATCH, so editing a lead means the same thing here and on the
  Forecast page.
- Switching tabs clears sort/search/hidden-columns/selection: selection is by row id and ids
  don't survive a table switch, and column keys are table-specific.

### `table-layout: fixed` needs the table sized to its columns

The board is `table-layout: fixed` (exact widths, and layout stays cheap on a 21-column board
since the browser never measures cell contents). But under fixed layout a `width: 100%` table
**redistributes any shortfall or overflow proportionally across the columns**, which silently
undoes every resize drag. The table is therefore sized inline to `44 + Σ column widths` and
scrolls; the CSS `min-width: 100%` still fills the panel when the columns are narrower than it.
Same family as this file's two earlier `table-layout: fixed` notes on the correlation matrix —
that property makes declared widths authoritative only if nothing else is fighting the total.

Widths live on the `<th>` cells rather than a parallel `<colgroup>`: under fixed layout the two
are equivalent, and it's one less structure to keep in sync with the hidden-column set.

### Verification: the preview pane throttles style recalc

Verified live 2026-08-08 by DOM/JS against `leadlens-frontend`; `tsc --noEmit` clean; the
impeccable detector reports one finding in the new code (`transition: width` on the search box,
kept deliberately — one small control, one 220ms user-triggered step; the codebase already has
two other instances).

**A real trap hit during this work, worth remembering.** Mid-verification the resize drag
appeared broken: the inline `style` said `276px` while `getBoundingClientRect()` *and*
`getComputedStyle()` both still reported `176px`. That looks exactly like a table-layout
invalidation bug, and was misdiagnosed as one (briefly switching the board to
`table-layout: auto` before the real cause surfaced). It is not. The Browser pane wasn't
compositing frames — screenshots were failing with "the Browser pane is not displayed" the
whole session — so **style recalc was throttled and even `getComputedStyle` returned stale
values**. Forcing a synchronous flush (`el.style.display='none'; void el.offsetHeight;
el.style.display=''`) made every measurement correct immediately. When measuring layout in a
non-compositing pane, flush first and flush the *right subtree* — an early check read a node
outside the flushed table and wrongly concluded the light theme's `--surface` wasn't swapping.
This is [[Preview-Pane-Viewport-Unreliable]] biting in a new way: not just viewport width, but
computed style itself.

Related: `Blob.text()` strips a leading BOM per the UTF-8 decode spec, so the CSV export's BOM
(there so Excel reads Khmer names as UTF-8) reads as absent unless verified via `arrayBuffer()`.
Confirmed present as `ef bb bf`.

## Ad performance / Combined export became writable too, 2026-08-08

The board shipped with editing and Delete on the Leads tab only — `lead_events` was the one
table with row-level write endpoints. Extended the same day so all three tabs behave alike.
Both ad-performance tabs are two views over the same `daily_ad_performance` rows, so one
endpoint pair serves both: `PATCH`/`DELETE /api/dataset/ad-performance/{row_id}` →
`update_ad_performance_row()` / `delete_ad_performance_row()` in `core.py`.

**Retraining is scheduled, not inline — and that difference is the point.** `update_lead_event`
calls `rebuild_aggregates()` + `train_models()` synchronously. That is wrong for this board:
`train_models()` measures **~18s** here, and inline cell editing fires one request per committed
cell, so a five-cell correction would block for a minute and a half. The ad-performance endpoints
instead reuse the existing single-flight background guard `_request_retrain(tasks)` (`app.py`) —
the same one the change-event recorder uses — and the frontend arms the existing
`useRetrainWatcher` so a `.board-retrain-chip` shows in the section header while it runs. Without
that chip the forecast would silently be stale for ~18s after every edit.
`rebuild_aggregates()` is deliberately **not** called: it reads only `lead_events`, so an
ad-performance edit cannot change what it computes.

**Not every column is editable, and the exclusions are load-bearing:**
- **`leads`** — the board's Leads value is `daily_ad_set_aggregates.lead_count`, joined in by
  `get_dataset_rows()`. `daily_ad_performance.leads` is a real column but is ~always NULL and
  attribution-broken. Writing it would change *nothing the user can see*, which is worse than
  refusing the edit.
- **`cost_per_messaging_conversation_started`** — rendered as a COALESCE of the stored value and
  spend/messages, so a hand-entered figure is silently overridden whenever the stored one is NULL.
- **`ad_id` / `fb_ad_title`** (export tab) — no backend column at all; they are `-` placeholders
  because the importer's ad-grain rollup discards per-ad identity.
- **The five declared variables** — attached in Python after the query, same reason they can't be
  sorted or filtered.

The allowlist lives in `AD_PERFORMANCE_UPDATE_FIELDS` (`core.py`) and is mirrored by the
`AdPerformanceUpdate` pydantic model (`app.py`) and the per-column `edit` flags in
`DATASET_ROW_COLUMNS` (`App.tsx`). Three places, one contract — a column added to one and not
the others either silently no-ops or 422s.

**`UNIQUE(day, campaign_id, ad_set_id)` is reachable from the UI.** Those three columns *are*
editable, so retyping a Day onto a date that ad set already has raises `sqlite3.IntegrityError`.
`update_ad_performance_row()` catches it and re-raises a `ValueError` naming the actual
conflicting day/campaign/ad set, which `app.py` turns into a 422 and the board shows in
`.board-error` while rolling the optimistic edit back. Verified live: editing Jun 6 → Jun 7 on
an ad set that already had Jun 7 produced "Another row already covers 2026-06-07 / campaign … /
ad set …. Each ad set has one row per day." and the cell reverted to Jun 6.

`commitBoardField` now takes the **column**, not a field name, and derives the value's shape from
`column.edit` — so adding an editable column needs no change to the commit path. `LeadEditableCell`
gained a `date` type for `day` (stored as `YYYY-MM-DD`, exactly what a date input wants).

Verified live 2026-08-08: the editable/read-only split on both tabs matches the allowlist exactly;
a Reach edit round-tripped 1,292 → 1,500, persisted, and re-rendered through the column's own
formatter; the retrain chip appeared; Delete removed a purpose-built throwaway row via the API and
`daily_ad_performance` returned to its original 879 rows. All test mutations were restored.
`tsc --noEmit` and `npm run build` clean; the impeccable detector reports no new findings.

**Known limitation, matching existing precedent:** the retrain chip only reflects retrains this
page initiated. One triggered elsewhere (another tab, another client) won't show here —
`useRetrainWatcher` has always behaved this way, and `ChangeEventButton` shares the behaviour.
Also worth knowing: a re-import of the same `(day, campaign_id, ad_set_id)` upserts, so a manual
correction here is overwritten by the next upload covering that row.

## Declared-variable columns relabelled to raw identifiers, 2026-08-11

Per request, the five declared-variable columns in the "Raw data" board now show their
backend/model identifier instead of prose copy. `label` only — every `key`, the sort
allowlist, and the backend contract are untouched, so this is purely what the header
reads:

| was | now |
| --- | --- |
| Ad set age | `days_since_adset_started` |
| Ad change recency | `ad_change_recency` |
| Ad set change recency | `ad_set_change_recency` |
| Ad set change type | `ad_set_change_type` |
| Ad change type | `ad_change__type` |

Applied to **both** column arrays in `DATASET_COLUMNS` (`App.tsx`) — `ad_performance`
and `ad_performance_export` — since the two tabs share these five trailing columns and
splitting the naming between them would be the odd outcome.

Widths grew with the longer strings (106→210, 148→168, 168→192, 156→176, 148→156) so no
header truncates at its default width. Users can still resize; these are just the
defaults.

**Two things worth knowing:**

- `.dataset-rows-table th` sets `text-transform: uppercase` (styles.css), so these render
  as `DAYS_SINCE_ADSET_STARTED`, not lowercase. Underscores survive, so the identifier is
  still unambiguous, but it is *not* a copy-paste-exact match for the Python/SQL name.
  Left as-is rather than special-casing five headers out of the table's typography — if
  exact-case identifiers matter later, that's the rule to override.
- `ad_change__type` carries a **double underscore**, exactly as specified in the request.
  The actual backend column and the React `key` are both single-underscore
  `ad_change_type`. Flagged to the user as a probable typo; kept literal pending
  confirmation. If it was unintended, it's a one-word fix to the `label` and nothing else.

Deliberately out of scope: the Optimization page's decision table (`<th>Ad set age</th>`
and friends, ~App.tsx:5627) and `DATASET_DECLARED_SHORT_LABEL`, the 10-entry map behind
the correlation matrix's axis labels. Both still use the prose wording — the request named
the Dataset page's Raw data section specifically, and the matrix labels were *deliberately*
converted from identifiers to prose in the other direction (see the 2026-08-05 entry
above), so flipping them back silently would undo a considered decision.

Verified live on both the Ad performance and Combined export tabs; `tsc --noEmit` clean.

**Reordered same day, 2026-08-11.** The five columns were grouped by subject rather than by
kind. Order went

`days_since_adset_started, ad_change_recency, ad_set_change_recency, ad_set_change_type, ad_change__type`

to

`days_since_adset_started, ad_set_change_recency, ad_set_change_type, ad_change_recency, ad_change__type`

so each subject's recency/type pair sits together — the two ad-set columns, then the two ad
columns — instead of the ad-set and ad variables interleaving. Pure array reorder in both
`DATASET_COLUMNS` entries; no key, width, label, or render change. Verified live on both tabs;
`tsc --noEmit` clean.


## Change-type columns removed from the board, 2026-08-11

`ad_set_change_type` and `ad_change_type` are gone from **both** `DATASET_COLUMNS` arrays
(`ad_performance` and `ad_performance_export`), because the underlying declared variables 9 and
10 were deleted from the model entirely — see [[Change-History-Hand-Recording]]. The backend
stops emitting the keys at all (`_attach_declared_variables` now writes 4/6/7 only), so this is
not a hidden column, there is nothing behind it.

The board's trailing declared block is now three columns: `days_since_adset_started`,
`ad_set_change_recency`, `ad_change_recency`. The grouped-by-subject ordering from earlier the
same day is moot — with the type columns gone there is no interleaving left to fix.

`rawCategory` stays (recency values are still shown verbatim as `0_3_days` /
`no_recent_change`), but `formatChangeType` was deleted along with its last caller, the
Optimization decision table's two type cells. The declared correlation matrix's
`DATASET_DECLARED_SHORT_LABEL` map is down to 8 entries, so that matrix is 8x8 not 10x10.

Also removed: every `.change-type-*` rule in `styles.css` (trigger, portaled menu, its two
reveal keyframes, the reduced-motion guard, the option rows, and the Forecast/Dataset dark
scoping) — dead the moment the picker went.
