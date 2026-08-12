# Lead drilldown table — inline cell editing (Monday.com-style)

The lead verification table that opens when you click a date point on the Forecast
page's "Actual vs forecast" chart (`selectedLeadPoint` / `openLeadDrilldown` in
[App.tsx](../frontend/src/App.tsx)) was rebuilt 2026-08-08 to edit like a Monday.com
board: click any cell, it becomes a live input in place, blur/Enter commits
immediately, Escape reverts without saving. No more separate "open edit panel below
the table" flow.

## What changed
- Removed `editingLeadId` / `leadEditForm` / `startLeadEdit` / `setLeadEditField` /
  `saveLeadEdit` and the `.lead-edit-panel` form entirely.
- Added `commitLeadField(lead, field, rawValue)` — fires a single-field
  `PATCH /api/leads/{id}` (the backend's `LeadUpdate` already ignores unset fields via
  `exclude_unset=True`, so per-field PATCHes were always safe, just unused before).
  Updates `leadDrilldownRows` optimistically, rolls back and shows
  `.lead-action-error` on failure.
- New `LeadEditableCell` component (module scope, next to `MenuSelect`): a button that
  looks like plain table text until clicked, then swaps for an `<input>` in the same
  slot. Reused for customer name, created-at (datetime-local), campaign name/IDs, ad
  ID, ad title, and amount.
- Status is **not** a text cell — it reuses the existing `MenuSelect` dropdown
  (portaled, auto-flipping, same component the filter-row pickers use), skinned via
  `.lead-status-select.status-new` / `.status-existing` to look like a colored pill
  instead of the boxy filter-field look. Picking an option commits immediately.
- Delete (trash icon) is the only remaining row action; the pencil/edit icon is gone
  since every cell is directly editable now.

## Gotchas
- `LeadEditableCell`'s `formatDisplay(value)` receives the *committed* value, not the
  in-progress draft — draft state is local to the component and never leaks to the
  parent until commit.
- Cell padding on `.lead-drilldown-table td` was cut from `12px 14px` to `5px 6px`
  because `.lead-cell-display`/`.lead-cell-input` now supply their own internal
  padding; leaving both would have doubled the whitespace and broken the
  edge-to-edge Monday.com feel.
- Verified via DOM/JS dispatch in the Browser pane, not screenshots — the pane wasn't
  compositing frames in that session (see [[Preview-Pane-Viewport-Unreliable]]).
  Confirmed: click-to-edit swap, Escape-cancels-without-PATCH, and a real
  New→Existing→New status round-trip with no console errors.

## Legibility pass, 2026-08-08

Per request: text in this table (especially the Lead ID/Campaign ID/Ad set ID/Ad ID `<code>`
columns and the created-at timestamp) was too small and too dim to read comfortably. All under
`.lead-drilldown-table` in `styles.css`:

- Base row text: inherited the app-wide `td { color: var(--muted); font-size: 12px }` →
  now `color: var(--text); font-size: 13.5px` set directly on `.lead-drilldown-table td`.
- `code` (the four ID columns): `var(--dim)` 10px (the least visible color in the palette,
  meant for de-emphasized text) → `var(--text)` 12px.
- `small` (the created-at time-of-day): `var(--dim)` 9px → `var(--text)` 11px,
  `opacity: .75` added so it still reads as secondary to the date above it without going
  back to a hard-to-read grey.
- `b` (customer name) and `.num` (amount): bumped 12.5px/12px → 14px, already on
  `var(--text)`.
- Header row (`thead th`): kept close to its inherited size (9.5px → 10.5px) — not part of
  the request, headers are labels, not data being read row by row.

**"White" means `var(--text)`, not a hardcoded `#fff`.** `--text` resolves to `#f0ece0`
(near-white/cream) in the dark theme this table is normally viewed in, but to a dark navy in
the light theme — verified live via theme toggle + computed-style check, so the fix reads
correctly in both instead of going invisible on light mode's pale background.

Cascades to `LeadEditableCell`'s display button for free — `.lead-cell-display` was already
`color: inherit; font: inherit`, so plain-text cells (Campaign, Ad title, etc.) picked up the
new size/color from their parent `<td>` with no separate rule needed.

### Second round, same day — the first pass wasn't enough

Reported still too small/hard to see after the round above. The color fix had landed correctly
(verified both times via computed style, both themes) but a 12px monospace `code` element reads
visibly smaller and lighter than sans/display body text at the same pixel size — matching color
without matching perceived weight wasn't enough. This round treats the four ID columns as
first-class data instead of secondary metadata:

- Base row text 13.5px → **15px**, `font-weight: 500` added (was unweighted/inherited).
- `code` (Lead/Campaign/Ad set/Ad ID): 12px → **14px**, `font-weight: 600` added (was
  unweighted), `letter-spacing` un-tightened from `-.01em` to `0`.
- `small` (timestamp): 11px → **12.5px**, opacity nudged `.75` → `.8`.
- Customer name (`b`) and Amount (`.num`): 14px → **15.5px**, both `font-weight: 700`.
- `LeadEditableCell`'s button/input (`.lead-cell-display`/`.lead-cell-input`) grew to match —
  `min-height` 30px → 34px, input font 12px → 14px — so the click-to-edit input doesn't look
  cramped next to the now-larger display text.
- The status pill grew too, but **scoped to `.lead-drilldown-table .lead-status-select`
  specifically** — `.lead-status-select` is shared with the Dataset board's denser
  `board-table`, and that table wasn't part of this request. Verified live the board's pill
  stayed at its original 24px/9px after this change.
- Table `min-width` 1420px → 1560px so the bigger text has room without any cell overflowing
  its own box (`scrollWidth > clientWidth` checked per-cell after the change, none did) — the
  table still scrolls horizontally inside `.lead-drilldown-table`'s `overflow-x`, same as
  before, just at a wider natural size.

Verified live via computed-style checks on a real row for every column named above, in both
themes, plus confirmed the Dataset board's own inline cells and status pills were unaffected —
`tsc --noEmit` clean, no new impeccable-detector findings.

### "Created" split into separate Date and Time columns, 2026-08-08

Per request — was one column, the date with a small time-of-day sub-line stacked under it.
Now two `<th>`s ("Date", "Time"), each its own `LeadEditableCell` reading/writing the same
`created_at` field:

- Both derive from `dateTimeInputValue(lead.created_at)` (already used for the old
  datetime-local input) split into `datePart`/`timePart` at the `T`.
- **Each column's `onCommit` splices its own edited part onto the *other* column's current
  part** before calling `commitLeadField(lead, 'created_at', ...)` — editing Time sends
  `${datePart}T${newValue}`, editing Date sends `${newValue}T${timePart}`. No backend or
  `commitLeadField` change; both still write the one `created_at` field exactly as before,
  just assembled from two cells instead of one.
- `LeadEditableCell` gained a `'time'` type (native `<input type="time">`, no special
  handling needed beyond the type union) alongside the existing `date`/`datetime-local`.
- The now-unused `.lead-drilldown-table td small` rule (was the sub-line under the date) was
  removed rather than left dead — nothing in this table renders a `<small>` anymore.
- Table `min-width` bumped 1560px → 1640px for the extra column header + cell padding.

Verified live: editing Time from 01:25 → 14:45 updated only the Time cell (Date cell
unchanged, confirmed by string equality against its pre-edit value), persisted server-side as
`2026-07-04T14:45:00`, and was restored. No console errors, no error overlay.
Screenshot verification wasn't available in that session (pane not compositing) — confirmed
via DOM/network only; a visual check is still worth doing before calling this fully done.

### "Lead Quality" column added, 2026-08-08

New CRM pipeline-stage column, between Status and Date, per request. There's no import
source for it -- it's hand-recorded via the board, same as `status`, not derived from
anything uploaded. Six values, in pipeline order: **Intake** (default), **Not Qualified**,
**Qualified**, **Converted**, **Lost**, **Awaiting Document and Payment**.

- **Schema**: `lead_events` gained `lead_quality TEXT NOT NULL DEFAULT 'Intake'` via the
  same `ALTER TABLE ... ADD COLUMN` migration pattern already used for every other
  column added after initial launch (`core.py`, the block right after
  `model_backtest_metrics`'s migration). A constant `NOT NULL DEFAULT` backfills every
  existing row as part of the `ALTER` itself -- confirmed live, all 3,023 pre-existing
  leads read `Intake` immediately after migration, no separate `UPDATE` needed.
- **`LEAD_QUALITY_OPTIONS`** (`core.py`) is the one source of truth for the six values and
  their order, and had to be defined *above* `_LEADS_FILTER_FIELDS` (which reads it) --
  Python evaluates module-level code top to bottom, and this file's lead-editing helpers
  (`LEAD_UPDATE_FIELDS`, `_clean_lead_update_value`) happen to live thousands of lines
  below the dataset-rows table specs that also need the list. Caught this the hard way: an
  initial version defined it near the editing helpers, which is a natural place to look for
  it, and broke the app (`NameError` at import) until moved. The frontend has its own
  mirrored `LEAD_QUALITY_OPTIONS` array in `App.tsx` -- no shared-constant mechanism exists
  between the two languages here, so keep them in sync by hand if the list ever changes.
- **Validated server-side** in `_clean_lead_update_value`: an unrecognized value raises
  `ValueError` naming the six valid options, same pattern as every other field's cleaning
  step. Verified live: writing `"Bogus"` is rejected with the full options list in the
  message.
- **Every explicit `lead_events` column list needed the new column added by hand** --
  `SELECT *` picks it up for free, but two spots didn't use `*`: `GET /api/leads` in
  `app.py` (the exact endpoint the Forecast page's drilldown calls) and
  `update_lead_event()`'s own post-write `SELECT` in `core.py`. Missing the second one
  surfaced as a `KeyError: 'lead_quality'` reading `r['lead']['lead_quality']` after a
  successful write -- worth checking both whenever a new lead_events column is added,
  since neither failure is visible from the table schema alone.
- **Pill styling** (`.lead-quality-select`, `styles.css`) mirrors the existing Status pill
  pattern exactly -- same `MenuSelect`-as-colored-pill component, same drilldown-only size
  bump -- but on its own `quality-*` class namespace rather than reusing `.lead-status-
  select`'s `status-*` modifiers, so a future quality value can never collide with a status
  one. Six colors, chosen to read as a sequence rather than just "different": neutral
  (Intake) -> red (Not Qualified) / blue (Qualified) -> amber (Awaiting Document and
  Payment, reusing "New" status's existing pending-action amber) -> red again, stronger
  (Lost -- shares Not Qualified's color since both are drop-outs, but a heavier fill/border
  so a later, more terminal "Lost" doesn't read identically to an early "Not Qualified") or
  green (Converted, the win).
- **Deliberately not added to the Dataset page's "Raw data" board** (`DATASET_ROW_COLUMNS`,
  `DATASET_FILTER_FIELDS`) or its filter bar in this pass -- out of scope for what was
  asked. The backend plumbing (`DATASET_ROW_TABLES["leads"]["columns"]`, its
  `filter_fields`/`sort_fields`/`search_columns` entries) *was* extended, though, since
  `get_dataset_rows()` already serves the Dataset page's Leads tab and leaving a real
  column out of that response would have been a second kind of drift to fix later. The
  Dataset board will pick this column up automatically the day someone adds a
  `DATASET_ROW_COLUMNS.leads` entry for it -- no further backend change needed then.
- **Colors iterated once more, same day**: "Lost" was originally a dim/muted grey
  (fizzled-out framing); changed to red on request, sharing `--danger` with "Not
  Qualified" but at higher opacity/border-strength so the two stay visually
  distinguishable despite both reading "red."

Verified live: the column sits between Status and Date; opening the dropdown lists all six
options in pipeline order; selecting "Converted" recolors the pill green, persists as
`Converted` server-side, and was restored; an incidental leftover test row (from an earlier
manual round-trip) was caught via `GROUP BY lead_quality` and fixed back to Intake --
confirmed final state is all 3,023 leads at `Intake`. Both themes checked. `tsc --noEmit`
clean, no new impeccable-detector findings.

Linked from [[Home]].
