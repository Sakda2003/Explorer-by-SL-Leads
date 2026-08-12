# Change Event UI Recorder

Built 2026-08-04. An "Ad set change" popover sits next to Budget Scenario in the
Forecast page toolbar (`frontend/src/App.tsx`), backed by
`GET/POST/DELETE /api/change-events`. It writes the same `change_events` table that
[[Change-Log-Importer]] uploads into, with `source='confirmed'`, so both paths feed one
definition of a recorded change.

**Two bugs from the extraction, fixed 2026-08-06 same day, reported by the user as "the
UI looks broken and messy" and "clicking a date closes the panel instead of picking
it":**
1. **Outside-click regression.** The extraction accidentally copied the *simpler* of two
   near-duplicate "close on outside click" effects that existed in `ForecastPage` before
   extraction — the one missing the `if (target.closest('.mini-date-popover')) return;`
   exception. `SingleDatePicker`'s calendar renders through `createPortal(..., document.
   body)`, so it is never a DOM descendant of `changePanelRef`; without that exception,
   every date-cell click registered as "outside" and closed the whole popover before
   `onChange` could fire. Restored in `ChangeEventButton`'s pointerdown handler
   (`frontend/src/App.tsx`).
2. **Unstyled on the Dataset page.** The popover's dark polish (`#111417` background,
   `rgba(255,255,255,...)` borders on `.budget-popover`, `.change-type-menu`,
   `.mini-date-trigger`, `.metric-toggle`, and `SingleDatePicker`'s `scoped-dark` calendar)
   was all keyed off a `.forecast-v2-page` ancestor selector in `styles.css` — true when
   this popover only ever rendered inside `ForecastPage`, false once `ChangeEventButton`
   also renders inside `.dataset-page`, so on Dataset it fell through to unstyled base
   rules and looked inconsistent next to everything else. Fixed by duplicating the
   *popover-exclusive* selectors under `.dataset-page` too (not the ones Dataset's own
   campaign picker also uses — `.campaign-menu`, `.date-popover`, `.selector` stay
   Forecast-only on purpose, see [[Dataset-Page]]) and adding `.dataset-page` to
   `SingleDatePicker`'s `closest()` dark-scope check.

Also updated the same day: the popover's copy strings ("age is estimated from this ad
set's earliest data", "using detected changes", "disables detection entirely") described
the detector fallback that [[OLS-Declared-Ten-Variables]] removed the same session —
reworded to describe the current confirmed-only behavior (age/recency report as 0, no
fallback estimate).

**Extracted into a standalone `ChangeEventButton({ adSetId })` component, 2026-08-06**,
so the Dataset page's scope bar could get the same recorder next to its Campaign/Ad Set
ID controls (see [[Dataset-Page]]) without either page reaching into the other's state.
Previously all of this (button, popover, both tabs' drafts, the coverage fetch, the
start-date fetch, save/delete handlers) lived inline inside `ForecastPage()`, keyed off
that page's own `selectedId`. Now it's a self-contained component — its own state, its
own `useEffect` fetches against whatever `adSetId` the caller passes in, its own
save/delete calls — dropped in twice: `<ChangeEventButton adSetId={selectedId} />` in
Forecast's toolbar (unchanged behavior, same JSX, just relocated) and
`<ChangeEventButton adSetId={selectedAdSetId} />` in Dataset's `.dataset-scope-bar`,
right after the Ad Set ID lookup form. No backend change — same three endpoints
(`/change-events`, `/ad-set-start-dates`), same `change_events` table. Verified live:
opening the popover on Dataset with an ad set selected shows that ad set's real
recorded-change coverage (e.g. "0/56 days"), and the Forecast page's copy of the button
still works identically post-extraction. `tsc --noEmit` clean.

**A dated range is stored as ONE row keyed on its start day.** The end date is kept
only for coverage reporting — never read as a feature. This isn't a shortcut: the
state-resolution logic carries the state forward from the last event, so the recency
variables count up on their own and reset at the next recorded change. Writing a row
per day in the range would reset recency to 0 every day and destroy the variable.

**Why:** the weekly upload template ships those four columns blank, and Meta's export
carries no change log at all, so the only way they ever become fact is someone typing
them in.

**How to apply:** never "fill in" the recency columns directly — they're derived, not
stored. If a range needs to stop rather than carry forward, that's a new event, not an
end date. Recording anything switches that ad set off the detector entirely (see
[[Change-Log-Importer]]).

**Third tab, "Start date", added 2026-08-06** — records declared variable 4
(`days_since_adset_started`), not a change event. Architecturally different from the
other two tabs on purpose: a launch date is one fact per ad set, not a dated range or
a repeatable event, so it's a new dedicated table (`ad_set_start_dates`, PRIMARY KEY
`ad_set_id`) and a small CRUD set (`list_ad_set_start_dates` /
`save_ad_set_start_date` upsert / `delete_ad_set_start_date`) rather than another row
shape in `change_events`. The tab UI reflects that: one date field, no Type dropdown,
no From/To — just "Started on" + a single recorded-value row (`budget-table-row`
reused, not a list). `_days_since_start_values()` (`backend/core.py`) now checks a
cached `_confirmed_ad_set_starts()` map first and overrides the left-censored
earliest-upload-day estimate per ad set when present — same "recorded fact beats
detector" convention as `change_events`, cache cleared by the same
`_clear_change_caches()`. Closes the pin from
[[Master-Dataset]]/`Vault/Data-Pipeline/Master-Dataset.md`: the left-censoring gap
for ad sets already running before the earliest upload can now actually be fixed by
typing in the real date, not just flagged.

**Caught during backend testing:** `pd.Timestamp.utcnow()` is tz-aware; comparing it
directly against `pd.to_datetime()` (tz-naive) for the future-date guard raised
`TypeError: Cannot compare tz-naive and tz-aware timestamps` on every save. Fixed with
`pd.Timestamp.now(tz="UTC").tz_localize(None)` before comparing. Verified end-to-end,
both via direct `core.py` calls (age before/after a confirmed date: `[0,1,2,3,4]` days
→ `[36,37,38,39,40]` for a 2026-05-01 start against 2026-06-06 upload data, exactly
right) and through the live UI (save, see it listed, delete, see the empty state
return) — the mechanical Impeccable detector found nothing in the new code.

**Saving now triggers a background retrain, 2026-08-06.** All four write paths this
popover reaches (`POST`/`DELETE` on `/api/change-events` and `/api/ad-set-start-dates`)
schedule `train_models()` as a FastAPI background task via `_request_retrain`
(`backend/app.py`); the popover shows a "Retraining forecasts with this change…" note
(`.change-retrain-note`) while it runs, driven by the new `retraining` prop and the
parent page's `useRetrainWatcher`. The handlers inside `ChangeEventButton` needed no
change — they already all call `onChange?.()`, so the parent pages own both the refresh
key bump and the watcher arm. See [[OLS-Declared-Ten-Variables]] for the single-flight
guard and why this can't be a synchronous call (~18s portfolio-wide).

**A recorded date outside the observed data window is silently inert** — it saves fine
and lists fine, but joins against zero rows, so the variable it feeds stays flat and
gets dropped from the correlation matrix. Two of the live change events currently in
the DB have exactly this problem. Details and the diagnostic order in
[[OLS-Declared-Ten-Variables]].

## A change is a point event, not a range (2026-08-07)

**Reversal of the "one row keyed on the range's START day" design above.** A change is now
recorded on ONE date and the type indicator fires on that day only; every other day is
"no change". The From/To pair in the popover is a single "Date of change" field.

**Why the old design broke:** the type dummy was carried forward from the last event
(`_resolve_change_state`), so an event dated on or before the ad set's first observed day
pinned its indicator to 1 for the whole window — a constant column, dropped from the
correlation matrix as zero-variance. The user hit this twice. Carry-forward also
misrepresented what was written down: recording "budget change on Jun 6" was being read as
a claim about every day after it, when the analyst only meant that one day.

**What changed:**
- `_resolve_change_state` — recency still carries forward (0 on the event day, counting up);
  the type indicator is now point-in-time. The two halves answer different questions and
  now behave differently on purpose.
- `_change_state_as_of` (raw table) mirrors it exactly, so table and model can't disagree.
- `NO_CHANGE_LABEL = "no_change"` is what the raw table shows on a non-event day **of an
  ad set that has at least one recorded event of that scope**. Nothing recorded at all →
  still `None`/blank. "No change" is an assertion the change log makes; blank means nobody
  has said either way, and collapsing them would make an untouched ad set look audited.
- `save_change_event` takes one date; `end_date` is written equal to `event_date` and never
  read. The API model keeps `end_date` optional so an older client isn't 422'd — the value
  is ignored, not honoured (verified: posting end_date 2026-07-10 stored 2026-06-25).
- The baseline is no longer a **recordable** type — `CHANGE_TYPES_BY_SCOPE["ad"]` dropped
  `no_recent_change`, and `save_change_event` rejects any baseline value with an explanatory
  error. Recording "nothing changed today" would assert nothing while consuming the
  one-event-per-day slot a real change may need. Uploaded sheets may still *contain*
  baseline cells; both importers now skip them (counted as `no_change_rows` in the import
  report, not as `unknown_change_type`).
- `_model_dataset_change_events` lost its "only days the state actually changes" dedup —
  under point semantics two budget changes a week apart are two events, and the dedup would
  have silently dropped the second.
- `change_event_coverage` counts live days **with** an event; an uncovered day is now a
  positive "no change" assertion, not a gap to fill. Popover copy updated to match.

**Verified 2026-08-07** on the user's real ad set `120236217374900078`: matrix went 14 → 16
columns with `#9 budget change` and `#10 ad added` both present; raw table reads
"Budget Change"/"Ad Added" on Jun 6 and "No Change" on all 55 other days; recency 0→1→2→…
A two-event scratch test confirmed each event fires on its own day, recency resets at each,
and both types appear as separate matrix columns.

**Left alone:** `build_master_dataset.py` has its own offline carry-forward encoding for
these columns (`attach(...)` with a default). It builds the standalone `master_dataset.xlsx`
analysis artifact and does not feed the app, so it now differs from the app's semantics —
rebuild it only with that in mind. See [[Master-Dataset]].

**Tests caught up 2026-08-11, four days late.** `tests/test_pipeline.py` still asserted the
pre-2026-08-06/07 world — carried-forward types, stored ranges, reversed ranges raising,
a recordable baseline, and an inferring detector — so nine tests were red against correct
production code. Rewritten to the current semantics (tests only, no production change);
suite is green. Per-test before/after table in [[Change-History-Hand-Recording]].

## Redesigned via `/impeccable` (polish + animate), 2026-08-07

Per request to "align well with each and every component, minimal, interactive, animations,
clean." Scoped refinement, not a redesign -- same data flow, handlers, and hard-dark surface
convention (`.dataset-page .budget-popover` etc. stay fixed-dark regardless of theme, per the
existing comment at `styles.css` ~L390 -- deliberately unchanged).

**Alignment fix: the active tab used a flat white wash, not this component's own gold accent.**
`.dataset-page .metric-toggle button.active` / `.forecast-v2-page` equivalent (a page-wide
hard-dark override, `styles.css` ~L440) neutralizes the default `.metric-toggle button.active`
gold-fill into `rgba(255,255,255,.09)`. Every OTHER interactive element already inside this
exact popover uses gold for selection/emphasis -- the pencil icon, the record button, the
active dropdown option (`.change-type-option.active { color: var(--yellow) }`) -- so the
inactive-looking tab row was the one piece of the component not speaking its own accent
language. Fixed with a same-specificity, later-in-cascade override
(`.dataset-page .change-scope-toggle button.active` etc.) that restores `color: var(--yellow)`.

**Focal motion: a sliding pill indicator on the three-tab row**, replacing the instant
background swap. Position is driven by one CSS custom property, `--tab-index`, set inline
from `CHANGE_TAB_ORDER.indexOf(changeTab)` (`CHANGE_TAB_ORDER` is a new module-level const,
`['ad_set','ad','start_date']`) -- the indicator can't drift out of sync with the actual tab
because there's no separate "which tab is highlighted" state, just the one already driving
the panel. Since the three tabs are equal-width (`flex:1`, no per-label sizing), the slide is
exact pure-CSS math: indicator width `calc((100% - 12px)/3)` (container padding + 2 gaps),
translated `calc(var(--tab-index) * (100% + 3px))` where the `100%` inside `translateX`
resolves against the indicator's own box per the CSS transform spec. Verified via
`getBoundingClientRect()`: indicator and active-tab rects match to the pixel at all three
positions.

**Layout: Type + Date merged into one row.** Was two full-width stacked blocks (`TYPE` field,
then a separate `DATE OF CHANGE` + submit-button row) for what is conceptually one filing
action. Now `.change-entry-row` (`grid-template-columns: 1fr 1fr auto`) holds type, date, and
the submit button together, bottom-aligned (`align-items:end`) -- verified all three controls'
bottom edges land on the same pixel. "Date of change" label shortened to "Date" since it now
reads unambiguously next to "Type" in the same row; the Start-date tab's own "Started on"
label was deliberately left alone, since that date means something different (an ad set's
launch date, not a change's date) and merging its wording would blur that distinction.

**Empty-state collapse, not a redesign of the empty message.** The prior turn deleted the
explanatory copy but left an empty heading + empty table rendering when nothing was recorded.
Now `.budget-history-section` doesn't render at all until there's at least one item
(`changeEvents.length > 0` / `adSetStartDate &&`) -- more minimal than an empty box, and
consistent with "delete the explanation, don't just blank it."

**Supporting motion, all scoped to `.change-popover` so it can't leak into the unrelated
Budget-scenario/Model-Governance instances of the same shared classes
(`.budget-table-row`, `.budget-history-section`, `.budget-toggle`):**
- Recorded-row entrance: `.change-popover .budget-table-row` fades/rises in, staggered via a
  `--stagger` custom property (`Math.min(index, 6)`, capping the delay per animate.md).
- The history section itself gets the same entrance when it mounts.
- Type dropdown open: `.change-type-menu` scales in from the top (`scaleY(.94)→1`, 140ms).
- `.budget-toggle:active` (the outer "Ad set change" opener) gained the same
  press-feedback scale already used by every other button in this family
  (`.budget-table-add`, `.budget-popover-close`) -- this one rule is intentionally NOT
  scoped, since `.budget-toggle` is shared with the Budget-scenario opener and the fix
  is a pure consistency win there too, not a behavior change.
- All new animations respect `prefers-reduced-motion: reduce` (verified: the harness's
  browser runs with reduced-motion on by default, and the guarded rules correctly resolved
  to `animation: none` / `transition: none` under it).

Dead CSS removed: `.change-date-row` / `.change-date-row-single` (fully unused after the
merge into `.change-entry-row`) and their now-redundant `input` rule (SingleDatePicker never
rendered a raw `<input>`, so it was already unreachable).

Ran `node .claude/skills/impeccable/scripts/detect.mjs` over both changed files per the
skill's requirement -- zero findings fell inside the edited line ranges; all reported
findings pre-date this change (pre-existing Inter Tight usage, an unrelated card's
`border-left`, etc.).

## Popover flips upward when there's no room below, 2026-08-07

Reported: the popover required scrolling the whole page to see the rest of it. Diagnosed
live via `getBoundingClientRect()` before touching anything -- the popover's OWN content
never overflowed its box (`scrollHeight === clientHeight` at every size tested); the box
itself was simply positioned off-screen. `.budget-popover` always opened downward
(`top: calc(100% + 8px)`), and the toggle can sit anywhere on Dataset's long, scrollable
page -- low enough on the page and most of a ~300px popover renders below the viewport,
forcing the page (not the popover) to scroll to reach it.

Fixed by mirroring `SingleDatePicker`'s existing `reposition()` pattern (same file, ~L611)
rather than inventing a new one: measure the toggle's real position and the popover's real
height on open (`ChangeEventButton`'s new `useLayoutEffect`, keyed on `changePanelOpen`),
and flip to opening upward (`.opens-upward` class -> `top:auto; bottom:calc(100% + 8px)`,
reveal keyframe reversed to `card-reveal-up`) only when there's more room above than below.
A `ResizeObserver` on the popover body re-measures as its own content changes height
(switching tabs, a record appearing/disappearing) without needing every state that could
affect that height listed as an effect dependency -- same technique, lighter than
`SingleDatePicker`'s full fixed-position + portal setup, since this popover doesn't need to
escape a scrolling ancestor's clipping the way a calendar does.

Verified both directions on the user's real ad set (`120244852135450078`): toggle with only
~66px of room below (910px viewport) correctly got `opens-upward` and rendered fully inside
the viewport (`pageScrollNeededForPopover: false`); the same toggle with ~1470px of room
below (2400px viewport) correctly stayed anchored downward, unchanged from before.

**Not applied to the Budget-scenario popover**, which shares the same `.budget-popover`
CSS class and toggle pattern and likely has the identical bug -- only `ChangeEventButton`
got the measure-and-flip wiring, since that's the component that was reported. The
`.opens-upward` CSS rule is generic and would work there too if that popover's own toggle
ref/effect were added later.

## Type dropdown portaled out of the popover's own scroll clipping, 2026-08-07

Reported: opening the Type selector (both the "Ad set change" and "Ad change" tabs share
this one dropdown, driven by whichever scope is active) buried the options -- had to scroll
to see and pick one. Different bug from the popover-flip fix above, despite the similar
symptom: the dropdown's own content never overflowed (4 options fit comfortably under its
220px cap), it was getting clipped by `.budget-popover`'s OWN `overflow-y: auto` -- a normal
absolutely-positioned child that visually extends past a scrolling ancestor's box is clipped
by that ancestor's scrollbar, not the page's, regardless of the ancestor's own on-screen
position.

Fixed the same way as `SingleDatePicker`'s calendar (same file, ~L611) already solves this
exact class of problem: `.change-type-menu` now renders through `createPortal(..., document.
body)` with measured `position: fixed` coordinates (`ChangeEventButton`'s new `typeMenuPos`
state + `useLayoutEffect`, keyed on `changeTypeOpen`) instead of being a CSS-positioned child
of `.change-type-field`. Escaping to `document.body` means it's never inside any ancestor's
scroll box to be clipped by in the first place. Reuses the same up/down flip logic as the
popover fix (`opens-upward` class, reversed reveal keyframe) for when the Type field itself
sits too low for the menu to fit below it.

Both outside-click handlers (`changePanelOpen`'s and `changeTypeOpen`'s own) needed a
`.change-type-menu` exemption once the menu stopped being a DOM descendant of `changeTypeRef`
-- same fix already applied for `.mini-date-popover` when SingleDatePicker's calendar got the
same portal treatment originally.

**Deliberately does NOT use a `ResizeObserver` on the menu itself**, unlike the popover's own
flip effect (which does observe the popover) -- matches `SingleDatePicker.reposition()`
exactly: measure on open, plus window scroll/resize. The type menu's content (a short, fixed
option list) never changes size while open, so there's nothing for a self-observer to
usefully react to, and one was tried and removed during debugging (see below).

**Verification note for future work in this pane:** hit a reproducible, deterministic-per-
render but non-deterministic-across-attempts mismatch between the portaled node's inline
`style` attribute (always mathematically correct, confirmed via the trigger's own measured
rect) and what `getBoundingClientRect()` reported for it (frequently `(0,0)` with a
padding-only width, as if `top`/`left`/`width` were never applied) -- specifically for a
FRESH React `createPortal` insertion. Ruled out, in order: a self-referential
`ResizeObserver` loop (removed it, bug persisted identically); stale/duplicate DOM nodes
(`querySelectorAll` always found exactly one); a CSS specificity conflict (only one matching
rule, confirmed via live stylesheet enumeration); the fix-direction/transform math itself
(a manually created element, byte-for-byte same class/attributes/inline-style, always
rendered correctly). The decisive test: removing and re-appending the SAME already-broken
node (no attribute changes at all) immediately fixed its measured geometry. Combined with
`requestAnimationFrame` outright timing out earlier in the same session state, this points to
the automation pane's compositor being stalled while not displayed -- a React portal's first
commit doesn't get picked up by it, while a forced DOM move (or, for a real user, the
continuous paint pipeline of an actually-visible tab) does. Extends the existing
[[leadlens-preview-pane-viewport-unreliable]] finding: it's not just resize measurements that
can't be trusted here -- a freshly-portaled `position: fixed` element's very first
`getBoundingClientRect()` in this pane can't either. If this recurs, remove+re-append the
node (or just distrust the first read) before concluding the underlying code is wrong.
