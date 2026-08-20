# OLS Panel on the Forecast Page

Built 2026-08-05. The Spend-only + Multivariate OLS cards render on the Forecast page,
attached to the Actual-vs-Forecast chart. Dataset is the separate portfolio-wide diagnostic
surface after the Model Performance page was removed on 2026-08-12.

**Attached, not adjacent (2026-08-05, same day):** the cards render *inside*
`.tracking-chart-block`, the chart's own bordered container, as a sibling of the
`ResponsiveContainer` div rather than a separate section below it. They also lost their own
heading ("REGRESSION DIAGNOSTICS" / scope chip) — Sakda asked for the result only, no framing
text. The cards use `.tracking-chart-block`'s border/radius/background instead of their own.

**Position within the container (2026-08-05, same day again):** first child, above the
legend and chart — not below the chart. `.forecast-ols-block`'s divider is a `border-bottom`
(it was `border-top` when the block sat underneath), since it now separates itself from
what's below it, not above. `.tracking-chart-block` has `overflow: hidden` and
`border-radius: 10px`, so the block's own top corners get clipped by the parent for free;
it needs no radius of its own.

## Scope

`get_ols_model_summaries(ad_set_id=None, campaign_id=None)` fits at three scopes. The
Forecast page keys its fetch on **the same two values as the tracking chart above it**
(`selectedCampaignId`, `selectedId`), so the regression always describes exactly the leads
that chart is drawing. `ad_set_id` wins over `campaign_id`. Dataset calls it without scope
parameters for its portfolio-wide diagnostic read.

Because the Forecast page always has a campaign selected (it defaults to the first one), the
panel there is never portfolio-wide in practice.

**Scope narrows the population, never the encoding:**
- **Ad-set scope** gets the ad-set-grain branches — 0/1 change-type indicators, true
  monotonic age from `_days_since_start_values`' ad-set branch.
- **Campaign and portfolio scopes** get the pooled branches — change-type dummies are the
  *share* of live ad sets in each state (see [[OLS-Declared-Ten-Variables]] for why those
  coefficients look enormous). Keeping campaign on the pooled encoding is what makes a
  campaign fit comparable to the portfolio fit.

The spend frame is filtered **once** up front, so every downstream helper's "portfolio"
mean/share is automatically taken over the scope instead. `_ad_change_events` gained an
`ad_set_ids` tuple filter for the campaign case; its `period_start` / `last_day_by_set` are
still measured before that filter so left-censoring means the same thing at every scope.

## Small scopes are the real hazard here

The portfolio has 56 days against ~18 multivariate regressors. A single ad set can have
**one**. `_fit_ols_summary` refuses to fit below `max(12, len(features) + 6)` observations
and returns `None`, so a thin scope renders no card rather than a confident-looking fit with
one degree of freedom.

That silence needed explaining, so the response carries a `scope` block
(`observations`, `ad_set_count`, `multivariate_terms_wanted`, `multivariate_days_needed`)
and the UI turns it into specific copy:
- no days → "No leads recorded for this selection yet."
- under 12 days → "Only 1 day of data in this scope - a regression needs at least 12."
- spend-only fits but multivariate does not → a note naming the term count and the shortfall.
  No ad set in the current data hits this (they are either ~56 days or ~1), so it was
  verified synthetically: 15 days with 15 terms fits univariate and refuses multivariate.

**The scope chip is gone (2026-08-05).** With no chip, a thin scope's only signal is the
empty-state / partial-fit copy in `olsEmptyCopy` / `olsMultivariateNote` — those strings
carry the full day-count explanation now (`"Only 1 day of data in this scope..."`), since
nothing else on screen distinguishes a 14-day ad set's fit from the portfolio's.

## Structure

- `OlsResultCards` — module-level component in `frontend/src/App.tsx`, shared by Forecast
  and Dataset so the two cannot drift. Both current callers pass `coefficients={false}` for
  the compact four-stat read (R², Adj R², F p-value, RMSE) plus the card header. Per-term
  coefficients are available under the shared "Show detail" disclosure instead of burying
  the forecast chart.
- `olsStat` / `olsPValue` — module-level helpers used by the shared component.
- Placement is after the lead-drilldown block, not before it. In the normal (no drilldown)
  state that is still "directly under the chart"; when a dot is clicked the drilldown stays
  adjacent to the chart it belongs to instead of being pushed below the regression.

## New endpoint

`GET /api/ols-summary?campaign_id=&ad_set_id=` returns the `ols` payload plus the `scope`
block. It exists because `/api/model-metrics` also serializes ~1,256 per-ad-set backtest
rows — **1.09 MB** versus **9 KB** for the same regression. The Forecast page refetches on
every scope change, so it should never pay that.

Requests are guarded by a `olsRequestId` ref: switching campaigns quickly must not leave the
previous campaign's regression sitting under the new campaign's chart.

## Measured values (2026-08-05 data, 29 ad sets / 56 days)

| Scope | Spend-only R² | Multivariate R² | Terms |
|---|---|---|---|
| Portfolio | 0.485 | 0.780 | 18 |
| Campaign `…930078` (2 sets) | 0.323 | 0.578 | 15 |
| Ad set `…950078` | 0.311 | 0.563 | 15 |

Term count falls at narrow scope because `_select_multivariate_ols_features` drops
zero-variance features — a single ad set that never changed its targeting has no targeting
dummy to estimate. That is correct, not a bug.

**The `N vars` badge itself is gone from the Forecast page (2026-08-05).** It's gated behind
the same `coefficients` prop as the term table — Sakda asked for it removed after seeing it
next to the bare fit stats.

## A scrollbar that only showed up once the table left

`.model-gov-ols-fit` (the four-stat row) previously had `overflow-x: auto` for the
full-table layout, where the row can outgrow its column count on narrow screens. Setting `overflow-x`
also forces the browser's computed `overflow-y` from `visible` to `auto` — with no
coefficient table below to make the card tall, that combination was painting a stray
vertical scrollbar next to the four stats on the Forecast page (visible in the screenshot
that prompted this fix). `.model-gov-ols-fit.is-only` resets `overflow: visible` and lets
the row wrap onto two lines via `auto-fit` instead of scrolling.

## Theming

`.model-gov-ols*` rules in `styles.css` use the shared theme tokens, so the cards render
consistently in both the Forecast and Dataset views.

**Why:** the panel answers "does spend explain leads?" where people actually look at leads,
at the scope they are actually looking at.
**How to apply:** keep the OLS fetch keyed on the same values as the tracking chart — if the
two ever diverge, the page silently shows a regression for data it is not drawing. See
[[OLS-Declared-Ten-Variables]] for what the terms mean and why the change-type coefficients
look enormous, and [[OLS-In-Forecast-Selection]] for why this diagnostic is not what most ad
sets' forecasts actually use.

**"Show detail" toggle, 2026-08-07.** This panel is one of the two `coefficients={false}`
call sites of `OlsResultCards`, so it picked up the new "Show detail" button and full
statsmodels-style expanded printout (summary block, coefficient table with split CI columns,
residual diagnostics) automatically — no change needed here beyond what already existed.
Full details in [[OLS-Declared-Ten-Variables]].

## Four functional forms, not one (2026-08-19)

The Spend-only card no longer reports a single linear fit. `OlsFormComparison` renders under
its four fit stats: one row per functional form (linear / quadratic / log / sqrt), with R²,
adjusted R², AIC and P>F, and the AIC winner marked. Full reasoning, including why all four
share one row set and why the winner carries a significance caveat, is in
[[Univariate-Spend-Functional-Forms]].

Two consequences for this note:

- **`ols.univariate` is still the linear fit**, so nothing that reads it broke — but it is now
  fitted on the scope's *positive-spend* days rather than every day in the scope. For three
  ad sets that lowers `no_observations`. That is the price of making the AIC column beside it
  mean anything.
- **The "Measured values" table above is stale** for that reason: those spend-only R² figures
  were measured on the old all-days row set, on 2026-08-05 data.

`scope.spend_days` was added alongside `observations`, because "days in this scope" and "days
this regression could use" are now different numbers. The empty-state copy branches on it: an
ad set with 48 days of leads and no spend at all now reads "No spend recorded against this
selection, so there is nothing for a spend regression to fit" instead of the old, misleading
"Upload ad performance data". Twelve of thirty ad sets are in exactly that position.
