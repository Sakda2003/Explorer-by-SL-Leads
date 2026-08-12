# Spend Against Leads Scatter

Built 2026-08-04, redesigned same day. Sits on the Forecast page between the
actual-vs-forecast tracking chart and [[Budget-Optimization-Tab]]'s sibling "Spend
share vs lead share" block (`<section id="efficiency-scatter">` in
`frontend/src/App.tsx`).

**Current shape: daily portfolio grain, one dot per day.** X = that day's total ad
spend, Y = that day's total actual leads (`adSpend.daily`, already computed
server-side in `get_ad_spend_analytics` — no backend change was needed). Three flat
KPI chips sit above the plot: Total spend, Total leads, Blended CPL, all read straight
from `adSpend.summary`. Single accent color (`--yellow-strong`), soft filled blobs
(r=7, no stroke, `fill-opacity` 0.62), no legend, no benchmark line. Given directly
from a user-supplied reference screenshot (a generic light-mode analytics tool) — the
brief was the dot style and the three-KPI-card layout, not literally the color.

**Superseded design, worth remembering if this comes up again:** the first version
plotted one dot per **ad set** (not per day), colored by standing (efficient/par/costly
vs a benchmark CPL ray from the origin), with click-to-select wired into the page's
`selectedId`, dimming, and a full keyboard-accessible mirror list. That version solved
a real problem — 11 of 25 ad sets had leads with zero recorded spend, and plotting them
at x=0 both crushed the cloud into the corner and scored them as the *most efficient*
ad sets in the portfolio, which was wrong (spend and leads come from different tables;
see [[Ad-Decision-Engine]]). **The zero-spend/attribution-gap problem doesn't apply at
daily grain** — every day in `adSpend.daily` has both a spend and a leads figure, so
there's nothing to hold out. If a future ask reintroduces per-ad-set grain, re-read
that exclusion logic before shipping — the crowding and false-efficiency bugs are real
and will come back.

**"More dots" was resolved by changing grain, not by fabricating data.** The user
asked for more points; the honest lever was switching from ~14-25 ad sets (a hard cap)
to ~56 days in the recorded window, which was already sitting in the API response
unused. Don't pad point count by, e.g., jittering or including partial-attribution
rows — grain change is the correct move when "more dots" is the actual ask.

**Theming note still applies:** the Forecast page is a hard-scoped dark surface in
both app themes, so `--scatter-spend` is a page-scoped alias to `--yellow-strong`
rather than a raw hex, keeping it inside the token system.

**Trend line added 2026-08-04**, same day: an ordinary-least-squares fit over the
plotted days, drawn only across the observed spend range (`min` to `max` of the actual
data), never extrapolated back to $0 — a regression line has no business claiming what
happens at spend levels the portfolio hasn't shown. Reuses the app's existing dashed
"reference line" language (`var(--series-median)`, same treatment as the daily-budget
line on the spend chart) rather than inventing a new line style. The caption below the
chart translates the slope into plain language ("roughly N more/fewer leads for every
$10 more spent in a day") instead of showing the raw coefficient — units of
leads-per-dollar mean nothing to a non-analyst, but a $10 increment does.

**Straight OLS line replaced with a LOESS curve, 2026-08-06**, per feedback that a
single slope can't show *where* returns plateau or bend — which was the actual
question. `loessCurve()` (App.tsx, near `olsStat`/`olsPValue`) is a from-scratch
locally-weighted linear regression: for each of 60 evenly spaced x-samples across the
observed spend range, fits a weighted line using only the nearest `k` points
(`k` = 60% of n, minimum 3), weighted by a tricube kernel so nearby points dominate.
Falls back to no curve below 4 points or a zero-width spend range — a local
neighborhood isn't stable below that, same reasoning the old line used `n >= 2` for a
single global slope. Negative fitted y is clamped to 0 (a local dip near a low-spend
cluster is a fitting artifact; leads can't be negative). Rendered as a recharts
`<Line>` (not `<ReferenceLine>`, which only draws straight segments) fed the sampled
grid points, `type="monotone"`, same dashed `--series-median` styling as the line it
replaced — mixing `<Line>` into a `<ScatterChart>` works because recharts'
scatter/composed chart types share one coordinate system across child series, as long
as the Line's data objects carry the same field names as the shared X/Y `dataKey`s
(`spend`/`actual_leads`).

**Wired to the page's selection filter, same day.** Originally sourced its points from
raw `adSpend.daily` (always portfolio-wide) and its KPI chips from `adSpend.summary`
(also portfolio-wide), so it sat among scoped charts as the one that never moved when a
campaign or ad set was selected. Fixed by switching both to `spendDaily` /
`filteredSpendSummary` — the same memoized, already-filtered values the tracking chart
and "Total spent per day" chart use — instead of building a second filtering path.
A `.scatter-scope` label (reusing the existing `spendScopeLabel`) now shows what's
selected, matching the `cpl-trend-scope` pattern above it. Verified live: switching
campaigns changes total spend/leads/CPL and every plotted point; entering an ad set ID
(commits on Enter, not on keystroke — `completeAdSetLookup`) scopes to that ad set
alone; clearing the ad set ID falls back to the campaign level, not to portfolio-wide.

**Animations + hover interactivity added 2026-08-04, same day.** Three things:
- The KPI chips now use the app's existing `AnimatedNumber` component (previously only
  used by the page-level `Metric` cards) instead of static text. Generalized it to take
  a `format` prop (defaults to `fmt`) so it can render `money`/`cplMoney` too, not just
  plain counts — that's the only change to the shared component.
- `.scatter-plot` is `key={spendScopeLabel}`, so switching campaign or ad set forces a
  full remount: grid, axes, trend line, and dots all replay the app's one shared
  `card-reveal` keyframe together, and Scatter's own entrance animation reruns for the
  new point set. Deliberately reused the existing keyframe rather than inventing one.
- Dots get a hover scale (1.4x, via `transform-box: fill-box` since these are raw SVG
  `<circle>`s) plus a drop-shadow glow in `--scatter-spend`, matching the existing
  `.tracking-click-dot:hover` glow convention elsewhere on this page rather than a new
  hover language.
- All of it is properly gated: `prefers-reduced-motion: reduce` kills the entrance
  animation and the hover transition (but not the hover *state* itself — scale/glow
  still apply, just without easing, per the rest of the app's pattern).

**Axis label overlap fixed 2026-08-04, same day.** The Y-axis title ("Daily leads")
was a rotated (`angle: -90`) recharts label at `position: 'insideLeft', offset: 18` —
that offset pushes a rotated label *rightward, into the plot*, which at this chart's
40px-wide Y-axis column landed it directly on top of the tick numbers (visibly
overlapping "16" in a real screenshot). Fixed by dropping the rotation entirely and
using a non-rotated, corner-pinned label (`position: 'insideTopLeft'`) instead — a
short horizontal tag in the top-left corner structurally cannot collide with tick
numbers running down the left edge, whereas a full-height rotated title sharing that
same narrow column always risks it. Also bumped tick font 11→12.5px (contrast 7.67:1 →
10.56:1 against the page's `#0b0c0d`) and axis-label font 11→11.5px (6.73:1), and
widened the Y-axis column 40→48px for breathing room. **If a Y-axis title is ever
needed on a narrow chart on this page again, use the corner-pinned pattern, not a
rotated one** — the rotated version needs real horizontal room (~50px+) that this
page's compact charts don't have.

**Trend line showing through dots, fixed same day.** Not a z-order bug — this recharts
version paints by zIndex-layer class, not JSX order (`ReferenceLine` → layer 400,
`Scatter` → layer 600, confirmed both by class name and by actual DOM position), so
the dots already painted above the line. The real cause: dots are intentionally
translucent (`fillOpacity: 0.62`, for the density-blending look), so the line still
shows through wherever it passes under one — worst exactly where the OLS fit is best,
i.e. the dense cluster it's drawn through (verified geometrically: 9 of 56 dots have
their center within one radius of the fitted line). Fixed by giving each dot an opaque
page-color (`#0b0c0d`) knockout circle underneath the translucent color circle, both
now grouped in one `<g className="scatter-dot">` — **grouped, not two independent
circles**, because the hover-scale CSS targets `.scatter-dot` itself; two separate
circles would let the color fill grow on hover while the opaque knockout stayed fixed
size, exposing a ring of line/grid around the edge. The knockout doesn't affect
dot-over-dot blending (still visible density where multiple translucent color circles
overlap each other) since it only ever blocks what's *behind* a dot, not other dots.

**R² and P-value KPI chips: added, then reverted same day (2026-08-06).** Briefly
added two more `.scatter-kpi` tiles (client-side OLS fit over the plotted points,
matching this page's own "Spend-only OLS" card at the same scope -- verified R²
0.232 both places). Removed same day per feedback; the chart is back to Total
spend/Total leads/Blended CPL only. If this comes back: the regression math itself
was correct (a real two-tailed t-test on the slope via a regularized-incomplete-beta
implementation, not an approximation) -- what's not obvious without a second look is
that it only ever matches "Spend-only OLS" at the *same* scope, never "Multivariate
OLS" (more variables, always reads higher) or another page's portfolio-wide fit
(different data). Re-derive from git history rather than re-deriving from scratch if
asked for again.

Related: [[Ad-Decision-Engine]], [[CPL-Trend-Chart]] (same `spendDaily` source, now
shared three ways), [[Stack-and-Build]].
