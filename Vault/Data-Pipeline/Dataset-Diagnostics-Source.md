# Dedicated Dataset diagnostics source

The Dataset page's correlation matrix and multivariate OLS use a dedicated,
versioned snapshot store. They do not read from `lead_events`, `daily_ad_performance`,
`model_dataset`, or another operational table, and there is no fallback to those sources.
The Forecast page remains on its existing `/api/ols-summary` path, but activating a
diagnostic snapshot also refreshes the operational spend and budget inputs used by Forecast.

## Import contract

Managers and admins can import `.xlsx`, `.xlsm`, or `.csv` snapshots from either the
Dataset page or the general Upload page. The dedicated workbook signature is the exact
20-column Meta/lead schema headed by `Campaign name`, `Ad set ID`, `Lead Amount`, `Day`,
and the remaining diagnostic variables. `Lead Amount` is the OLS outcome.

Each successful import is stored as a complete, immutable version in
`diagnostic_dataset_imports` and `diagnostic_dataset_rows`. The source file name, SHA-256,
row counts, date range, validation summary, upload time, and activation time are retained.
Only one version is active. Activation is transactional, older versions remain available
for rollback, and importing the same file hash reuses its existing version.
Activation mirrors the active snapshot into `daily_ad_performance` using an internal
`diagnostic_dataset_spend` upload row, then rebuilds derived budget periods from `Ad Set
Budget` and retrains models. Existing campaign IDs are resolved from prior ad-performance
or lead rows by ad set/campaign name when possible; otherwise campaign name is used as the
scope key. Reactivating an older diagnostics version repeats the same sync so Forecast,
budget scenario, spend charts, and Dataset ad-performance boards move with the active
snapshot.

The UI previews a snapshot before activation and shows its validation blockers, warnings,
active version, source hash, import date/time, coverage, freshness, combined or duplicate
grain keys, and history. Freshness is based on the original upload time, so reactivating
an old version does not make it appear newly refreshed.

## Validation and grain

The canonical row grain is `Day + Campaign name + Ad set ID`. Missing required headers,
invalid required values, Day/Date mismatches, weekday mismatches, negative metrics, and
duplicate groups with conflicting `Lead Amount` values block activation. Duplicate export
fragments with the same `Lead Amount` are combined into one row per grain: spend and
additive counters are summed, rates are recomputed where their components are available,
and `Reach`/`Frequency` are left `NULL` for combined rows because audience reach is a
unique-person metric. Optional numeric blanks stay `NULL`; they are not silently changed
to zero. Correlations use pairwise-complete observations and report their sample sizes.
OLS uses complete cases and reports its actual observation count.
OLS also returns each excluded term with a specific constant, complete-case, or stable
rank-dependence reason, and explains when a scope has too few observations to fit safely.

The supplied `Ad-Performance-06-06--28-09.xlsm` previews as 1,158 rows from 2026-06-06
through 2026-09-28, covering 11 campaigns and 11 ad sets. It contains 39 duplicate grain
groups, which roll up to 1,119 diagnostic rows and can be activated with an aggregation
warning.

## API surface

- `POST /api/dataset-analysis/preview`
- `POST /api/dataset-analysis/confirm`
- `GET /api/dataset-analysis/status`
- `GET /api/dataset-analysis/imports`
- `POST /api/dataset-analysis/imports/{import_id}/activate`
- `GET /api/dataset-analysis/scopes`
- `GET /api/dataset-analysis/correlation`
- `GET /api/dataset-analysis/ols`

All Dataset correlation and OLS responses include active-version provenance. The frontend
also verifies both responses name the same import version before displaying them.
