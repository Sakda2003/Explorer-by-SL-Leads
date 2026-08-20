# LeadLens / Customer Traffic Forecasting — Project Vault

This vault is the curated, human+Claude-maintained knowledge base for this project.
It exists so that Claude Code can read a handful of short notes instead of re-deriving
project history from source code, session transcripts, or the (deleted) SESSION_HANDOFF
files. **Update a note here after every change to this project** — code, data pipeline,
config, or docs — not only after something "non-obvious" or large (standing instruction,
2026-08-04). Don't let this go stale, and don't let it balloon into a second copy of the
codebase.

## Architecture
- [[Stack-and-Build]] — how the app is built/run, dual-theme system, deploy topology

## Data Pipeline
- [[Master-Dataset]] — the 10-variable ad-set×day table and which variables are inferred proxies
- [[Model-Dataset-Template]] — the weekly upload workbook Sakda fills in
- [[Model-Dataset-Upload-Type]] — lead-grain workbook upload (leads + context + change events)
- [[Change-Log-Importer]] — how uploaded change logs override inferred detectors
- [[Change-Event-UI-Recorder]] — the in-app "Ad set change" popover
- [[Ad-Set-Ad-Lookup-Combine]] — one-off script merging ad-set-day performance with lead-grain Ad ID/Title lookup

## Modeling
- [[OLS-Declared-Ten-Variables]] — the multivariate OLS panel on the 10 declared variables
- [[Univariate-Spend-Functional-Forms]] — the spend-only regression fitted four ways (linear / quadratic / log / sqrt), ranked by AIC on one shared row set; 18 of 30 ad sets get all four, the other 12 never spent
- [[Change-History-Hand-Recording]] — the 29-ad-set manual change backfill; directional budget types, and recency becoming a 5-bucket categorical
- [[Forward-Selection-In-The-App]] — what picks the Multivariate OLS card's variables: greedy search, two entry gates (adjusted R² gain + block F p < 0.10), the per-round "Selection path" trace, and the backtest that kept it out of the forecast path
- [[Forward-Selection-Notebook]] — two notebooks: `forward_selection_all_variables.ipynb` (current, every variable, whole-block categoricals, forward + backward) and the superseded `forward_selection.ipynb`
- [[Per-Ad-Set-Regression-Report]] — `build_regression_report.py`, the standalone HTML report ranking all 30 ad sets by multivariate adjusted R²; "univariate" means per-variable here, not spend-only
- [[Forecast-Flatness-Is-The-Data]] — why the 14-day forecast looks flat, measured 6 ways
- [[Forecast-Flatness-Fix]] — the original 2026-07-23 flat-forecast bug fix
- [[OLS-In-Forecast-Selection]] — the OLS panel is a diagnostic, not what every ad set's forecast uses; which 3 of 29 ad sets it actually wins

## Features
- [[Budget-Scenario]] — what-if budget panel + dated elasticity fitting
- [[Ad-Decision-Engine]] — boost/cut verdicts on the Ad Performance / Optimization page
- [[Budget-Optimization-Tab]] — CPL-vs-budget response signal, merged into the same page
- [[CPL-Trend-Chart]] — the daily spend-per-day chart on the Forecast page
- [[Forecast-Page-OLS-Panel]] — the OLS cards under the forecast chart; scoped to the selected campaign/ad set, and why thin scopes refuse to fit
- [[Lead-Management-Page]] — the CRM workspace for rating leads through the six pipeline stages; funnel + filters + bulk rating, and why rating deliberately skips the retrain
- [[Lead-Drilldown-Inline-Edit]] — the date-click lead table now edits inline per cell, Monday.com-style, instead of a separate edit-panel form
- [[Spend-Leads-Scatter]] — spend-vs-leads scatter with a benchmark-CPL ray; why $0-spend ad sets are held out
- [[Access-Control]] — three auth topologies (Cloudflare Access / Tailscale Serve / Basic Auth), Docker deploy phases, hosting choice, and the Railway demo move
- [[Dataset-Page]] — data inventory, variable dictionary, correlation matrix, and raw-row browser; portfolio-wide audit surface before trusting the forecast
- [[Model-Performance-Removal]] — Dataset is now the sole portfolio-wide diagnostics surface
- [[UI-Component-Inventory]] — current app pages and the pre-redesign component catalogue
- [[Upload-Commit-Bar]] — the Upload page's import action, moved out from under the fold into a sticky bar
- [[Dual-Theme-Redesign]] — the 2026-08-12 redesign: gold + cyan, a light theme that finally works, and the 531 literals that were blocking it

## Known Gotchas / Reference
- [[Combined-Ad-Set-Import]] — Combined-Ad-Set-Dataset files feed `daily_ad_performance` (graphs) through the existing ad-performance importer; model_dataset stays the source for lead names
- [[Meta-Export-XLSX-Not-CSV]] — always take Meta exports as XLSX
- [[Guessed-Adset-IDs-Duplicates]] — why imports must retire superseded guessed-ID rows
- [[Leadlens-Ad-Export-Grain-And-Budget]] — ad-grain rollup rules and the budget-column question
- [[Budget-Conflict-Trailing-Window]] — conflict detection uses a trailing 14-day window
- [[Preview-Pane-Viewport-Unreliable]] — don't trust the Browser pane's resize measurements
- [[Screenshotting-The-App]] — the Browser pane can't screenshot when hidden; use Playwright + Edge, with the selector traps listed
- [[Uvicorn-Reload-Hangs]] — `uvicorn --reload` silently serves stale code here; backend auto-reload goes through the watchfiles CLI in `.claude/launch.json`
- [[Money-Formatter-Rounded-To-Whole-Dollars]] — `money()` lost cents everywhere until fixed 2026-08-06
- [[Lead-Spend-Fallback-To-Ad-Performance]] — leads' Amount column went blank 2026-08-01+ once uploads stopped being the lead-grain model-dataset workbook; now falls back to `daily_ad_performance`
- [[Retrain-Debounce-And-GIL-Contention]] — why a single cell edit froze the board for ~31s, and the three separate costs behind it (inline retrain, UI busy-gate, GIL)
- [[CSP-Inline-Script-Hash]] — the CSP allows the one inline theme script by sha256 hash, keeping 'unsafe-inline' off script-src
- [[Model-Dataset-Export-For-Migration]] — `export_model_dataset_chunks.py` re-exports the live DB as re-importable model_dataset workbooks, chunked by date, for migrating history into another deployed instance
- [[Customer-Traffic-Created-Updated-Alias]] — traffic exports with "Created"/"Updated" headers (no "At") were rejected by the type detector, which never consulted the header-alias map read_tabular already used; also: missing `pyjwt` locally causes "Failed to fetch" on Upload, not an import-logic bug

## Maintenance convention
- Update after **every** change, not just big or non-obvious ones — see CLAUDE.md at
  the project root, which states this as a standing rule Claude Code must follow
  before ending any turn that touched code, data pipeline, config, or docs.
- One note per topic, named for what it's about, not when it happened.
- Link related notes with `[[wikilinks]]` instead of restating their content.
- When something is superseded, edit the note in place and say so — don't leave two
  notes disagreeing with each other.
- Code facts (file/function names, line numbers) drift as the codebase changes — verify
  against the current source before trusting an old note's specifics.
