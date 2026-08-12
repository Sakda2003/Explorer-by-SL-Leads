# Dual-theme redesign (2026-08-12)

Requested because the app "looked plain and boring", with ten reference designs supplied:
a dark financial dashboard, the Prodexa sidebar, a Knockturnals dark invoice app, the
Spark Pixel light dashboard, a KPI-card set, a Transactions column picker, a matplotlib
correlation matrix, and four Monday.com boards. Scoped from the component catalogue in
[[UI-Component-Inventory]].

**Decisions taken:** both themes with a working toggle; all seven pages at the time; gold stays the
brand/action colour with **cyan** added for data series and selection, green/red reserved
for semantic good/bad.

**Allocation-panel alignment, 2026-08-12:** The Forecast page's "Spend share vs lead share"
panel now uses a local 16px inline inset for its row labels, legend, funding-status column,
and selected-campaign summary. Bars and separators remain full width; this is visual spacing
only and does not change allocation data or interactions.

**Campaign-ranking alignment, 2026-08-12:** The Forecast page's "Lead share by campaign"
and "Actual cost per lead" panels now use matching 16px outer content insets. Campaign labels,
percentage/CPL values, selected-campaign details, and range controls no longer sit against card
edges; ranking data, benchmark marks, tracks, and interactions are unchanged.

**Forecast header simplification, 2026-08-12:** Removed the right-aligned campaign/ad-set
context and date-range label from the Forecast header. Selection state and date bounds remain in
place for the chart filters and lead-drilldown context; this only removes the redundant header copy.

**Shell simplification, 2026-08-12:** Moved the accessible dark/light toggle from the topbar to
the sidebar footer, replacing the retired Explorer workspace card. Removed the topbar breadcrumb
entirely; the theme preference continues to persist and the control remains available from the
mobile navigation drawer.

**Day/night control, 2026-08-12:** Redesigned the sidebar theme toggle as a stateful pill:
Night mode places a light moon dial on the left of a dark track, while Day mode places a sun dial
on the right of a soft neutral track. The button keeps its saved-theme behavior, target-state
label, keyboard support, and pressed state.

## What the problem actually was

Not taste — structure. Three palettes were fighting inside `frontend/src/styles.css`:

1. the token block at the top (orange-gold `#e0994a`),
2. **531 hardcoded colour literals** below it (`#0b0c0d`, `#c9a86a`, `#7fbf8f`) that
   actually won on screen,
3. a light theme that could never render, because `index.html` hardcoded
   `data-theme="dark"`, there was no toggle, and only 33 light override rules existed.

So the enabling step was de-hardcoding. Until those literals became tokens a light theme
was physically impossible and every visual change had to be made twice by hand.

## How the sweep was done

Property-aware, not luminance-only: a colour's correct token depends on what it is *doing*.
`#1c1f21` appeared 40 times as a border, so it is `--line`, not `--surface`. The classifier
keyed on (CSS property, HSL) together. Two traps worth remembering:

- **HLS saturation blows up at extreme lightness.** `#f4f2ee` — the app's primary text
  colour, 24 uses — reads as S=22% and was initially sorted into the gold family. Classify
  neutrals by *chroma* (max−min over 255), not saturation.
- **`mask-image` uses colour syntax to mean opacity.** Tokenising the `#000` in the ambient
  grid's fade mask would have broken it. Masks must be excluded from any colour sweep.

Result: 531 literals → 1 (that mask stop). All 33 stale light overrides deleted — once a
rule is tokenised its override is redundant, and leaving both means the override silently
wins. One of them had already gone toxic: `.sidebar` background resolved to
`color-mix(var(--text) 92%)`, which is near-*black* once `--text` is theme-aware.

## What changed visually

- **Shell:** grouped nav (Analyze / Data / System), filled gold pill for the active row
  replacing a gradient + left bar + two glows, a workspace card pinned to the bottom, and
  a new sticky topbar carrying the breadcrumb and the theme toggle.
- **Board surfaces:** Monday-style **solid full-bleed status cells**. The fill is painted on
  the `<td>`, not the trigger — a table cell only stretches its child when it has a definite
  height, and these rows are sized by a density setting.
- **Columns popover** gained a search field and lock icons; the **filter builder** gained a
  row-count line in its header.
- **Optimization** verdicts became solid labels; benchmark bars became outlined tracks.
- **Correlation matrix** now ramps to full saturation (was capped at 90%, which is why it
  looked washed) with a labelled colourbar and white ink past 0.45 magnitude.
- **KPI cards** gained delta chips; the 7-day move used to be buried mid-sentence.

## Gotchas found along the way

- `main:has(.upload-v2-page)` was a row-flex centring container, and the new topbar is a
  child of `main` — so the topbar collapsed to the upload panel's width on that one page.
  Stack the container instead.
- A `--surface-*` token is wrong for a foreground mark. The Optimization sparklines used
  `background: var(--surface-2)` after the sweep and vanished on a white card. Marks belong
  on the text ramp.
- Two "primary" buttons were `#e8e6e1` fills — deliberately *inverted* light-on-dark
  buttons. Read as surfaces by the sweep, they became white-on-white. Restored as an
  explicit inverted style (`background: var(--text); color: var(--canvas)`).

## Verification

Contrast was solved for, not eyeballed: every foreground token clears WCAG AA body
(≥4.5:1) against both its canvas and its surface in both themes, and every status fill
clears AA against its own ink. Four tokens needed correcting — light `--dim` was an outright
fail at 2.88:1, and light `--gold` was large-text-only at 3.67:1, exactly as predicted.

All seven pages were captured in both themes at the time using the Playwright recipe in
[[Screenshotting-The-App]] (the capture script takes `--themes=dark,light` and sets
`localStorage` before reload).

## Artifacts

- Before/after, all seven pages at the time in both themes, plus the palette table:
  https://claude.ai/code/artifact/5c5774aa-df01-409c-8b38-383f5ea888ce
- The pre-redesign component catalogue this was scoped from:
  https://claude.ai/code/artifact/6276bc0d-9679-4974-99ca-9a413bcb5fd1

## Reference-led visual-only polish (2026-08-12)

The follow-up request was explicit: **do not change features, interactions, data flows,
or structural page layouts**. The work therefore touched **only**
`frontend/src/styles.css`; `App.tsx` and the backend were not edited.

- Forecast retains its existing five KPI cells, controls, charts, and sequence. The visual
  skin adds restrained dark panel material, clearer card separation, and gold/cyan emphasis
  consistent with the analytics-dashboard reference.
- Dataset retains the current raw-row board, column picker, filter builder, sort, row
  density, inline edits, selection, and status behavior. Its table, header, menu, and
  correlation surfaces were made more spreadsheet-like with no DOM or behavior change.
- Upload retains its exact drop/browse flow and fields. The empty drop zone now reads as a
  deliberate upload state through surface, border, icon, and action styling alone.
- Shell refinements are purely material: sidebar, workspace card, topbar, hover, and active
  navigation treatments use the existing tokens and dimensions.

Verification: `pnpm build` passed after the stylesheet change. The local app was visually
checked on Forecast, Dataset, and Upload in dark mode; the Dataset board was also checked in
light mode. Existing data loaded, board status cells rendered, the theme toggle still switched
and restored, and the browser console had no errors.
