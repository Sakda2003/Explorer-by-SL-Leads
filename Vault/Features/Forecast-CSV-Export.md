# Forecast CSV Export

The Forecast page header has an **Export CSV** action for the daily data behind **Amount spent
and lead volume over time**. It is a browser-side export: the `/api/dashboard/ad-spend`
response already contains the campaign-day and ad-set-day rows, so exporting does not make a
second request or introduce a second aggregation path.

## Interaction

`ForecastCsvExport` opens a focused, portaled dialog. It supports:

- Campaign or ad-set scope, with searchable multi-select and select-all for the visible results.
- Campaign choices use the approved 18-name Forecast campaign catalog and resolve each name to
  its real IDs from both normalized campaign insights and daily spend rows. The export does not
  expose legacy/paused names, raw duplicate-ID entries, unattributed entries, or generated
  fallback labels such as `Campaign 850078`. A catalog campaign can still map to multiple
  underlying IDs; selecting it includes rows for every mapped ID and uses the approved name in
  the CSV.
- Ad-set choices use the exact Ad Set ID as the primary label and its resolved campaign as
  secondary context. Campaign ownership is resolved from the lead-derived ad-set mapping first,
  then the approved campaign ID map and source name. Missing source attribution is labeled
  `Campaign not attributed` instead of displaying a generated campaign name. Search matches
  either the full Ad Set ID or its campaign, and multi-select/select-all remain available.
- One, two, or any larger number of selected scopes.
- Independent from/to dates. The dialog starts from the Forecast page range, clamped to the
  actual spend-data range so future forecast dates do not produce a misleading filename. The
  date control reuses the Forecast range calendar with month navigation, highlighted start/end
  and in-range days, plus draft Reset, Cancel, and Apply actions. Its popover is portaled and
  viewport-aware so the export dialog cannot clip it.
- A live selected-scope and output-row count. Download stays disabled until the selection and
  range produce at least one row.
- Escape/backdrop close, restored trigger focus, background `inert` state while open, visible
  focus styles, reduced-motion handling, and token-driven light/dark themes.

The dialog keeps the visible controls to scope selection, date range, export summary, and the
download action. Column details stay in this documentation rather than being shown in the UI.
Its visual hierarchy uses a readable 12–14px control and metadata scale, roomier list rows,
44px-class inputs, and a fixed header/footer with a scrollable body so the workflow remains
clear and usable on shorter viewports.

The active campaign or ad set is preselected when the dialog opens. Switching between Campaigns
and Ad sets intentionally clears the selection rather than guessing an equivalent scope.

## File format

Campaign export columns are: Date, Campaign ID, Campaign, Amount Spent (USD), Actual Leads.
Ad-set exports add Ad Set ID. Rows are sorted by date, campaign, and ad set. Spend is written to
two decimal places, UTF-8 BOM is included for Excel, and cells are quoted. Values beginning with
spreadsheet formula prefixes (`=`, `+`, `-`, `@`, tab, or carriage return) receive a leading
apostrophe to prevent CSV formula injection.

The filename records the chosen scope and range, for example
`forecast-ad-sets-2026-08-01-to-2026-08-14.csv`.

Related: [[CPL-Trend-Chart]], [[Spend-Leads-Scatter]], [[Dual-Theme-Redesign]].
