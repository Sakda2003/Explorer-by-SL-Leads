# CPL / Spend-Per-Day Trend Chart

Shipped 2026-07-26 as "Cost per lead over time," then changed same day to **"Total
spent per day"** (dataKey `spend`) per user request. CSS classes still carry the
`cpl-trend-*` names — cosmetic legacy, not renamed.

**Key architecture fact worth remembering:** the chart **reuses the existing
`spendDaily` memo** in `App.tsx`, which already computes per-day values and is already
filtered by the current selection/date range. That's why selecting a campaign or
ad set moves all three Forecast-page charts together with zero new data plumbing —
only a tooltip, a card, and CSS were added.

**Alignment convention:** the three stacked Forecast-page charts (spend, CPL, forecast
tracking) are kept horizontally aligned by sharing identical plot-area insets (left
YAxis width 68 + margin.left 0; right inset 72). If you change any one chart's YAxis
width or margins, change all three or the dates stop lining up.

**Forecast toggle:** the tracking chart used to always render the forward 14-day
horizon, breaking alignment with the other two charts. A `showForecast` toggle
(default off) now clips the default view to the last actual date and hides the
forecast render blocks until switched on.

**Diagnostic snapshot sync, 2026-10-01:** activating a dedicated diagnostics snapshot
now mirrors its spend and budget values into `daily_ad_performance` and derived budget
periods. The chart still reads `GET /api/dashboard/ad-spend`, but that endpoint now sees
the uploaded snapshot through the normal operational spend table, not a chart-only patch.
See [[Dataset-Diagnostics-Source]] for the snapshot import contract.

See [[Ad-Decision-Engine]] and [[Leadlens-Ad-Export-Grain-And-Budget]] for the underlying
spend/leads data model.
