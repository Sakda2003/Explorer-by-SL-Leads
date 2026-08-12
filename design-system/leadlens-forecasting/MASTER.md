# LeadLens Design System

**Project:** LeadLens Forecasting for Explorer by SL
**Updated:** 2026-08-12 — rewritten from the shipped tokens after the dual-theme redesign.
**Category:** Internal analytics dashboard

> Every value in this document was read out of `frontend/src/styles.css`, not out of intent.
> The previous revision of this file had drifted far enough to be actively misleading (it
> documented DM Sans and `#FFD400`; the code had neither). If you change a token, change it
> here in the same edit.

## Visual direction

A dense analytics tool, not a marketing surface. Dark is the primary theme — a near-black
ground with gold as the brand and action colour and cyan carrying the data. Light is a
designed counterpart, not an inversion: white cards on a cool grey ground with soft layered
shadows, the gold deepened until it is legible as text on white.

**The colour contract, in one line:** gold means *brand, action, or "you are here"*. Cyan
means *this is data*. Green and red mean *good* and *bad*, and are never used decoratively.
Nothing else gets to be saturated.

Themes are CSS variables on `:root[data-theme="dark"|"light"]`. The choice persists to
`localStorage['leadlens-theme']`; an inline bootstrap in `frontend/index.html` stamps the
attribute before first paint so there is no flash. The toggle lives in the topbar.

## Colour tokens

| Role | Token | Dark | Light |
|---|---|---:|---:|
| Page ground | `--canvas` | `#0C0D0F` | `#F4F5F7` |
| Sidebar | `--sidebar-bg` | `#090A0C` | `#FFFFFF` |
| Brand plate | `--brand-bg` | `#090A0C` | `#16181C` |
| Card / panel | `--surface` | `#16181C` | `#FFFFFF` |
| Raised / header | `--surface-2` | `#1D2025` | `#F8F9FB` |
| Hover wash | `--surface-tint` | `rgba(255,255,255,.045)` | `rgba(16,20,28,.04)` |
| Primary text | `--text` | `#F2F1EE` | `#14181F` |
| Secondary text | `--muted` | `#9BA0A8` | `#5A6270` |
| Tertiary text | `--dim` | `#7C828C` | `#676F7C` |
| Brand / action | `--gold` | `#C9A86A` | `#866B28` |
| Accent strong | `--gold-strong` | `#E0C285` | `#7C6021` |
| Accent muted (bars) | `--gold-dim` | `#8A7442` | `#B49A5E` |
| Ink on gold | `--ink-on-gold` | `#16130B` | `#FFFFFF` |
| Data series | `--cyan` | `#4FC3D9` | `#0E7894` |
| Data series strong | `--cyan-strong` | `#7FDCEC` | `#0A6076` |
| Success | `--success` | `#5BC08C` | `#17805A` |
| Danger | `--danger` | `#E2685C` | `#B3352C` |
| Warning | `--warn` | `#E0A93C` | `#9A6B10` |
| Hairline | `--line` | `rgba(255,255,255,.09)` | `rgba(16,20,28,.10)` |
| Accent border | `--line-accent` | `rgba(201,168,106,.34)` | `rgba(154,123,46,.34)` |
| Shadow ink | `--shadow-ink` | `0 0 0` | `16 20 28` |

Shadows are written `rgb(var(--shadow-ink) / .4)`. Pure black at 40% is correct on
near-black and far too heavy on white — this is why the token exists.

**Board tokens:** `--board-head`, `--board-row`, `--board-row-alt`, `--board-row-hover`,
`--board-line`.
**Nav tokens:** `--nav-active-bg` / `--nav-active-ink` / `--nav-active-border` — the active
sidebar row is a solid gold pill in dark and a gold *tint* with deep-gold ink in light,
because the solid fill goes olive on white.
**Correlation tokens:** `--corr-cold` / `--corr-zero` / `--corr-hot` — isolated from the
semantic palette so the heatmap's red does not read as "bad".

### Status tokens (Monday-style solid labels)

Each is a `-fill` / `-ink` pair: `--status-{new,existing,good,warn,bad,neutral,info,lost}`.
Solid fills, never tinted outlines — the point of the pattern is that a column of statuses
reads as a colour field you can scan without reading any of the words. On the Dataset board
the fill is painted on the `<td>`, not the trigger, so it goes genuinely edge-to-edge
regardless of the row-density setting.

### Contrast

Every foreground token clears **WCAG AA body text (≥4.5:1) against both its canvas and its
surface, in both themes**, and every status fill clears AA against its own ink. Verified
2026-08-12; the audit script lives in the redesign scratchpad. `--dim` and the light `--gold`
are the tight ones — do not lighten either without re-running the check.

## Typography

- **Display and body:** Inter Tight Variable
- **Numbers, IDs, uppercase labels:** JetBrains Mono Variable
- Both are **self-hosted** via `@fontsource-variable/*`, imported in `main.tsx`. There is no
  runtime font-CDN dependency — do not reintroduce a Google Fonts `@import`.
- Scale: `--text-xs` 10 → `--text-2xl` 32. Tabular numerals wherever metrics appear.
- Labels: 9–11px uppercase, tracked `.14em–.16em`.

## Shape, elevation, spacing

- **Shape scale — four steps only:** `--r-sm` 6px, `--r-md` 10px, `--r-lg` 14px,
  `--r-pill` 999px. (This replaced twelve ad-hoc radii.)
- **Elevation:** `--elev-1` / `--elev-2`. Dark is a border plus a faint inner highlight —
  the old 18px backdrop blur read as noise at this density and was removed. Light is layered
  soft shadows.
- 4/8px rhythm; gutters 28px desktop / 14px mobile; sidebar 252px; topbar 56px.

## Shell

Sidebar nav is **grouped** — Analyze / Data / System — with a workspace card pinned to the
bottom. Above the page content sits a sticky topbar carrying the breadcrumb and the theme
toggle. `main:has(.upload-v2-page)` stacks rather than rows, so the topbar still spans.

## Charts

- Actual series `--cyan`; spend and forecast `--gold`; semantic bars green/red.
- All Recharts colours are passed as `var(--token)` strings — never hex — so charts fully
  re-theme on toggle.
- Grid `--grid-line`, axes `--axis-line`, cursor `--cursor-line`.
- The correlation heatmap ramps to **full** saturation with a labelled colourbar; cells past
  0.45 magnitude flip to white ink. (The old 90% ceiling was why it looked washed.)

## Do not

- Write a raw hex or `rgba()` below the token block. There is exactly one left in the file
  (a `mask-image` stop, which is opacity, not colour).
- Add a `:root[data-theme="light"] .thing` override. All 33 of the old ones were deleted;
  if a rule needs to differ by theme, add a token instead — that is what `--nav-active-bg`
  and `--brand-bg` are for.
- Use a `--surface-*` token for a foreground mark. Sparklines, bars and dots belong on the
  text ramp; a surface token makes them vanish on white.
- Encode state with colour alone; ship layout-shifting hover; hide focus states.

## Protected details

Per the standing note in `Vault/Architecture/Stack-and-Build.md`: the KPI sparklines, the
uppercase eyebrow labels, the card-header accent stripes and the dense micro-typography are
**part of the look**, not slop. A previous app-wide sweep that removed them was reverted in
full. Restyle them; do not delete them.

## Release checklist

- [ ] Both themes pass WCAG AA for text — re-run the contrast audit, don't eyeball it
- [ ] Charts fully re-theme on toggle (grid, axes, series, tooltips)
- [ ] No new raw hex below the token block
- [ ] Captured in both themes across all seven pages before sign-off
