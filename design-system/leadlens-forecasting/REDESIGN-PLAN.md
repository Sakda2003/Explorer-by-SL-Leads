# LeadLens Visual Redesign — Full Plan

**Date:** 2026-08-12
**Status:** Steps 0–4 and 8–9 complete · Steps 5–7 partially complete (remaining work listed under each)
**Artifacts:** [Before/after](https://claude.ai/code/artifact/5c5774aa-df01-409c-8b38-383f5ea888ce) · [Pre-redesign component catalogue](https://claude.ai/code/artifact/6276bc0d-9679-4974-99ca-9a413bcb5fd1)

---

## 1. Context — why this was needed

The app worked but looked plain. The root cause was **not taste, it was structure**: three
competing palettes were fighting inside `frontend/src/styles.css`.

1. A proper token block at the top (`:root[data-theme="dark"|"light"]`) using a warm
   orange-gold `#e0994a`.
2. A second, **hardcoded** palette written directly into rules further down — `#0b0c0d`
   canvas, `#c9a86a` gold, `#7fbf8f` green — which is what actually rendered on screen.
3. A light theme that was **dead code**: `frontend/index.html` hardcoded
   `data-theme="dark"`, there was no toggle, and only 33 light override rules existed
   across 4,886 lines.

### Measured starting state

| | count |
|---|---:|
| Hardcoded hex + `rgba()` literals below the token block | **531** |
| — in shell + Forecast CSS | 206 |
| — in upload + misc CSS | 239 |
| — in ad-performance CSS | 75 |
| — in board CSS | 10 (already clean) |
| `var(--token)` uses | 1,174 |
| Distinct `border-radius` values | 12 |
| Light-theme override rules | 33 |

The enabling work was therefore de-hardcoding. Until those 531 literals became tokens, a
light theme could not physically render, and every visual change had to be made twice by
hand.

### Reference designs supplied

Ten images, mapping to the app as follows:

| Reference | Applies to |
|---|---|
| Financial Performance Dashboard (dark) | Forecast — KPI tiles, chart panel discipline |
| Prodexa sidebar (navy + lime pill) | App shell — nav rail, active state, grouped nav, footer card |
| Knockturnals invoice app (dark) | App shell — topbar, card material |
| Spark Pixel dashboard (light) | Overall light-theme layout, KPI cards with sparklines |
| KPI cards with progress + delta chips | Forecast — KPI card content |
| Transactions "Columns" popover (light) | Dataset — column picker |
| Monday.com ×4 (filter, sort, board, CRM) | Dataset / Optimization / Data History — board surfaces |
| matplotlib correlation matrix | Dataset — heatmap and colourbar |

### Decisions taken

- **Themes:** both, with a working toggle.
- **Scope:** all six current pages.
- **Accent:** gold stays the brand/action colour; **cyan** added for data series and
  selection; green/red reserved for semantic good/bad.

---

## 2. Constraints that shaped the plan

- **No version control.** A 531-edit sweep with no undo was the single biggest risk.
  Addressed in Step 0.
- **A previous app-wide visual sweep was fully reverted** (2026-07-28) because it removed
  things considered part of the look. **Preserved throughout, not "cleaned up":** the KPI
  sparklines, the uppercase eyebrow labels, the card-header accent stripes, and the dense
  micro-typography.
- **One file each.** All UI is `frontend/src/App.tsx` (5,907 lines) and
  `frontend/src/styles.css` (4,886 lines). No component files — class names are the only
  seam, so every step is scoped by CSS section, not by file.
- Recharts colours were already passed as `var(--token)` strings, so charts re-theme for
  free once tokens are correct.
- Build: `export PATH="/c/Program Files/nodejs:$PATH"; cd frontend && pnpm build`

---

## Step 0 — Safety net ✅

Backed up `styles.css`, `App.tsx` and `index.html` to `.backups/` with the suffix
`.pre-redesign-2026-08-12`, outside `frontend/src` so Vite does not compile them.

`git init` was offered as the stronger option and not taken up; the file copies stand in.

**To roll the whole redesign back:** copy the three files from `.backups/` over their
originals and rebuild.

---

## Step 1 — Rewrite the token block ✅

One complete token set, both themes authored together, replacing the partial block.
Legacy token names (`--bg`, `--yellow`, `--series-actual`, …) were **kept as aliases**
pointing at the new values, because ~1,170 existing rules reference them — far safer than
renaming across two large files.

### Colour — dark

| Token | Value | Role |
|---|---|---|
| `--canvas` | `#0C0D0F` | page ground |
| `--sidebar-bg` | `#090A0C` | nav rail |
| `--surface` | `#16181C` | cards, panels |
| `--surface-2` | `#1D2025` | raised, hover, table header |
| `--text` / `--muted` / `--dim` | `#F2F1EE` / `#9BA0A8` / `#7C828C` | type ramp |
| `--gold` / `--gold-strong` | `#C9A86A` / `#E0C285` | brand, actions, active nav |
| `--cyan` / `--cyan-strong` | `#4FC3D9` / `#7FDCEC` | data series, selection |
| `--success` / `--danger` / `--warn` | `#5BC08C` / `#E2685C` / `#E0A93C` | semantic only |
| `--line` | `rgba(255,255,255,.09)` | hairlines |
| `--shadow-ink` | `0 0 0` | shadow base |

### Colour — light

| Token | Value |
|---|---|
| `--canvas` | `#F4F5F7` |
| `--sidebar-bg` | `#FFFFFF` |
| `--brand-bg` | `#16181C` (the gold eagle logo needs a dark plate) |
| `--surface` / `--surface-2` | `#FFFFFF` / `#F8F9FB` |
| `--text` / `--muted` / `--dim` | `#14181F` / `#5A6270` / `#676F7C` |
| `--gold` / `--gold-strong` | `#866B28` / `#7C6021` |
| `--cyan` / `--cyan-strong` | `#0E7894` / `#0A6076` |
| `--success` / `--danger` / `--warn` | `#17805A` / `#B3352C` / `#9A6B10` |
| `--line` | `rgba(16,20,28,.10)` |
| `--shadow-ink` | `16 20 28` |

Shadows are written `rgb(var(--shadow-ink) / .4)`. Pure black at 40% is correct on
near-black and far too heavy on white — that is the entire reason the token exists.

### Also added

- **Shape scale**, collapsing 12 ad-hoc radii to four: `--r-sm` 6px, `--r-md` 10px,
  `--r-lg` 14px, `--r-pill` 999px.
- **Elevation:** `--elev-1` / `--elev-2`. Dark is a border plus a faint inner highlight —
  the old 18px backdrop blur read as noise at this density and was dropped. Light is
  layered soft shadows.
- **Board tokens:** `--board-head`, `--board-row`, `--board-row-alt`, `--board-row-hover`,
  `--board-line`.
- **Status tokens:** `--status-{new,existing,good,warn,bad,neutral,info,lost}` as
  `-fill` / `-ink` pairs, so Monday-style solid status cells work on both grounds.
- **Nav tokens:** `--nav-active-bg` / `--nav-active-ink` / `--nav-active-border` — solid
  gold pill in dark, gold *tint* with deep-gold ink in light, because a solid gold fill
  goes olive on white.
- **Correlation tokens:** `--corr-cold` / `--corr-zero` / `--corr-hot`, isolated from the
  semantic palette so the heatmap's red does not read as "bad".
- `--font-serif`, which was referenced by the formula card but never defined.

### Typography

The stylesheet imported Inter Tight + JetBrains Mono from Google Fonts while
`--font-display` / `--font-body` still pointed at system stacks — the download was paid for
and never used. Both are now **self-hosted** via `@fontsource-variable/*`, imported in
`main.tsx`, with the CDN `@import` removed. Inter Tight for display and body, JetBrains
Mono for numerals, IDs and uppercase labels. This removes a runtime network dependency,
which matters for a Cloudflare-Access-gated internal app.

> ⚠️ `package.json` pins every dependency to `"latest"`, so the font install re-resolved
> the whole lockfile and also moved recharts 3.9.2 → 3.10.1 and vite 8.1.4 → 8.2.1. A
> baseline build was taken *before* the install so any later failure could be attributed
> correctly. Both pass. Pinning these properly is worth doing separately.

---

## Step 2 — De-hardcode the 531 literals ✅

Swept property-aware, **not** luminance-only: a colour's correct token depends on what it
is *doing*. `#1c1f21` appears 40 times as a border, so it is `--line`, not `--surface`.
The classifier keyed on (CSS property, HSL) together.

Mapping rules applied:

| Context | Rule |
|---|---|
| `border*` + neutral | `var(--line)` |
| `background` + neutral | `--canvas` / `--surface` / `--surface-2` by lightness; `>90%` → `--text` (an inverted button) |
| `color` / `stroke` / `outline` + neutral | `--text` / `--muted` / `--dim` by lightness |
| any warm hue, chroma ≥36 | `--gold` family |
| any green / red / cyan hue | `--success` / `--danger` / `--cyan` |
| `rgba(0,0,0,α)` anywhere | `rgb(var(--shadow-ink) / α)` |
| `rgba(255,255,255,α)` border | `var(--line)` |
| `mask-image` | **excluded** |

### Two traps worth recording

- **HLS saturation blows up at extreme lightness.** `#f4f2ee` — the app's primary text
  colour, 24 uses — computes as S=22% and was initially sorted into the gold family.
  Neutrals must be classified by **chroma** (max−min over 255), not saturation.
- **`mask-image` uses colour syntax to mean opacity.** Tokenising the `#000` in the ambient
  grid's fade mask would have silently broken the background.

### Result

**531 literals → 1** (the mask stop, correctly left alone). All 33 stale
`data-theme="light"` override rules were deleted — once a rule is tokenised its override is
redundant, and leaving both means the override silently wins.

One of those overrides had already gone toxic: `.sidebar`'s light background resolved to
`color-mix(var(--text) 92%)`, which is near-**black** once `--text` is theme-aware. It was
unreachable before, so nobody had seen it.

---

## Step 3 — Make the toggle real ✅

- `frontend/index.html`: removed the hardcoded `data-theme="dark"`; added a tiny inline
  bootstrap that reads `localStorage['leadlens-theme']`, falls back to
  `prefers-color-scheme`, and stamps `data-theme` on `<html>` **before first paint** so
  there is no flash. It also syncs `<meta name="theme-color">`.
- `App.tsx`: a `useTheme()` hook owns every change after that first paint, writing the same
  key and attribute. Sun/moon toggle in the topbar.
- `color-scheme` flips with the theme, so native scrollbars and form controls follow.

---

## Step 4 — App shell ✅

- **Sidebar active state** → a single filled pill, replacing a gradient + left bar + two
  glows all doing the same job four times over.
- **Grouped nav** with section labels: `Analyze` (Forecast, Optimization, Model
  Performance), `Data` (Upload Data, Data History, Dataset), `System` (Settings). The pages
  fall into three genuinely different jobs and the labels make that legible.
- **Workspace card** pinned to the bottom of the rail.
- **New sticky topbar** — the app had no persistent chrome above the page heading. Carries
  a breadcrumb (`Analyze › Forecast`) and the theme toggle, which needed a home that does
  not move between pages.

---

## Step 5 — Forecast page ◐ partially complete

**Done:**
- KPI cards keep their sparklines (protected) and gained a **delta chip** — the 7-day move
  used to be buried mid-sentence in the note line; as a chip it carries direction in colour
  and glyph.
- Series recoloured: actual → `--cyan`, spend/forecast → `--gold`.
- Sparkline marks moved off surface tokens onto the text ramp (they vanished on white).

**Remaining:**
- **Unify chart panel chrome** — small uppercase title top-left, controls top-right,
  identical padding and legend chip style on every panel. The Financial-dashboard
  reference's discipline is that every panel is the same frame; the app still varies.
- **Cut the scroll.** The page is ~8,000px tall. The smaller charts (CPL trend, scatter,
  campaign mix, CPL rank) should go into a 2-up responsive grid, as the reference does with
  its small-multiples row. This is the biggest usability win left on this page, and is a
  structural layout change rather than a restyle.
- Optional: a thin progress track on KPI cards that have a target.

---

## Step 6 — Board surfaces (Dataset, Optimization, Data History) ◐ partially complete

**Done:**
- **Solid full-bleed status cells** — Monday's actual signature. Solid fills, not tinted
  outlines: the point of the pattern is that a column of statuses reads as a colour field
  you can scan without reading any of the words.
  The fill is painted on the `<td>`, **not** the trigger, because a table cell only
  stretches its child when it has a definite height and these rows are sized by a density
  setting.
- **Optimization verdicts** became solid labels; **benchmark bars** became outlined tracks
  with a proper track behind the fill.
- **Columns popover** gained a search field (these tables run past 20 columns) and a lock
  icon on the column that cannot be hidden.
- **Filter builder** gained a row-count line in its header — it turns the panel from "what
  am I asking for" into "what am I getting".

**Remaining:**
- **Board group headers + left colour rail** with a collapse chevron and a per-group
  summary row, per the Monday references. The Dataset board has no grouping today, so this
  is additive rather than a restyle — worth confirming before building.
- **Drag handles** for column reordering in the Columns popover (the Transactions reference
  has them; the app has no reorder capability at all yet).

---

## Step 7 — Remaining pages ◐ partially complete

**Done:**
- Upload Data and Settings inherit the new shell, type scale, shape
  scale and card material, and render correctly in both themes.
- Fixed a real layout bug: `main:has(.upload-v2-page)` was a row-flex centring container
  and the new topbar is a child of `main`, so the topbar collapsed to the upload panel's
  width on that one page. The container now stacks.
- Restored two "primary" buttons that were `#e8e6e1` fills — deliberately *inverted*
  light-on-dark buttons. Read as surfaces by the sweep, they became white-on-white in
  light. Now an explicit inverted style (`background: var(--text); color: var(--canvas)`).

**Remaining:**
- The Upload dropzone is still a large empty rectangle. It reads as unfinished rather than
  inviting, and deserves a proper empty-state treatment.

---

## Step 8 — Correlation matrix ✅

- The cell ramp now reaches **full** saturation. It was capped at 90%, mixing toward the
  surface colour — which is exactly why it looked washed next to the matplotlib reference.
- Replaced the 70px two-segment strip with a **colourbar with labelled quarter ticks**
  (−1.00 / −0.50 / 0.00 / +0.50 / +1.00), so a cell's shade can be read back to a number
  instead of only "bluer" or "redder". Laid out horizontally because it lives in a header
  row, not beside a square plot.
- Cells past 0.45 magnitude flip to white ink at weight 600 — the reference's readability
  trick.
- Added `--corr-zero` so the midpoint of the diverging scale is the neutral ground the
  cells actually sit on.

---

## Step 9 — Verification and documentation ✅

### Contrast — solved for, not eyeballed

Every foreground token was measured against **both** its canvas and its surface, in both
themes, and every status fill against its own ink. Failures were corrected by solving for
the required luminance in HLS while preserving hue and saturation:

| Token | Was | Now | Note |
|---|---|---|---|
| light `--dim` | 2.88:1 | 4.65:1 | outright **fail** |
| light `--gold` | 3.67:1 | 4.64:1 | large-text only — predicted as the risk |
| light `--cyan` | 4.41:1 | 4.66:1 | just under |
| dark `--dim` | 3.85:1 | 4.59:1 | large-text only |
| dark `--status-existing` ink | 4.39:1 | 4.61:1 | against its fill |

All foreground tokens now clear **WCAG AA body text (≥4.5:1)** on both grounds in both
themes.

### Visual verification

All six current pages captured in both themes via Playwright driving Edge — the Browser pane's
own screenshot tool fails when the pane is not displayed. The recipe and its selector traps
are in `Vault/Gotchas/Screenshotting-The-App.md`; the capture script takes
`--themes=dark,light` and sets `localStorage` before reloading.

Backend was untouched throughout: if `pnpm build` succeeds and the app loads, nothing
functional can have regressed.

### Documentation

- `design-system/leadlens-forecasting/MASTER.md` — rewritten **from the shipped tokens**.
  The previous revision had drifted far enough to be actively misleading (it documented DM
  Sans and `#FFD400`; the code had neither).
- `Vault/Features/Dual-Theme-Redesign.md` — new note covering the whole job.
- `Vault/Architecture/Stack-and-Build.md` — palette, fonts, the now-real toggle, the
  `"latest"` pinning hazard, and the `.backups/` location.
- `Vault/Features/UI-Component-Inventory.md` — marked as the "before" record.

---

## The colour contract, in one line

**Gold** means brand, action, or "you are here". **Cyan** means this is data. **Green and
red** mean good and bad, and are never decorative. Nothing else gets to be saturated.

---

## Standing rules for future work

Recorded in `MASTER.md`'s "Do not" section:

1. **Never write a raw hex or `rgba()` below the token block.** There is exactly one left
   in the file, and it is a `mask-image` stop (opacity, not colour).
2. **Never add a `:root[data-theme="light"] .thing` override.** All 33 old ones were
   deleted. If a rule must differ by theme, add a token — that is what `--nav-active-bg`
   and `--brand-bg` exist for.
3. **Never use a `--surface-*` token for a foreground mark.** Sparklines, bars and dots
   belong on the text ramp; a surface token makes them disappear on white.
4. **Re-run the contrast audit** rather than eyeballing, and capture both themes across all
   six current pages before sign-off.
5. **Do not delete** the KPI sparklines, eyebrow labels, card-header stripes or dense
   micro-typography — an earlier sweep that did was reverted in full.
