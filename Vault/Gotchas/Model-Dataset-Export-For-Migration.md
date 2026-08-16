# Model dataset export for migrating into the deployed instance

`export_model_dataset_chunks.py` (repo root) reads directly from the live `data/leadlens.db`
(not the stale static files under `Dataset/Datsaa/`) and writes a series of `model_dataset`
upload workbooks to `output/model_dataset_export/`, one per date chunk: first 06-06 to 07-11,
then weekly (Sat-Fri, matching the existing `Dataset/Datsaa` split convention) through
whatever the latest date in the DB is.

Built 2026-08-15 to hand a colleague clone/deployed instance the same history: this dev
instance's DB had run ahead of the deployed one's uploads (through 2026-08-12 vs the static
export files stopping at 08-01).

Each workbook has two sheets, matching what `read_model_dataset_workbook` in `backend/core.py`
actually accepts -- **not** the older `build_model_template.py` shape (ad-set-day grain, 10
declared variables), which is a reporting artifact and is not itself re-importable (it has no
`customer_name`/`created_at` lead identity, so it fails `MODEL_DATASET_REQUIRED`):

- `model_dataset`: lead-grain, one row per lead (`platform`, `lead_status`, `created_at`,
  `updated_at`, `customer_name`, `campaign_name`, `campaign_id`, `ad_set_id`, `ad_id`,
  `fb_ad_title`, `amount_spent_usd`).
- `ad_set_days`: ad-set x day grain (`date`, `ad_set_id`, `spend`, plus reach/impressions/
  frequency/budget/etc). Required alongside the lead sheet -- a lead-grain sheet alone can
  only describe days that produced a lead, so days that spent and got nothing would silently
  vanish. See [[Zero-Lead-Days-Vs-Padding]].

Verified each written file round-trips through `core.read_model_dataset_workbook` /
`core.is_model_dataset_workbook` with 0 rows skipped/rejected before handing off.

**2026-08-15 update:** `ad_set_days` also carries `days_since_adset_started`,
`ad_change_recency`, `ad_set_change_recency` -- computed by calling the app's own
`core._attach_declared_variables` on the exported rows, so the values match exactly what the
Dataset page shows (confirmed `ad_set_start_dates` + confirmed `change_events` only, no
detector fallback since 2026-08-06). These three columns are **not** part of
`read_model_dataset_workbook`'s accepted schema -- `_read_ad_set_day_sheet` only reads the
`MODEL_TO_AD` columns, so they're silently ignored on import. They're informational only:
re-importing this file into another instance does NOT transfer the underlying confirmed start
dates / change events, so the values won't recompute the same there unless that instance's own
`ad_set_start_dates` (26 rows) and `change_events` (38 ad + 38 ad-set, all confirmed) are
migrated too -- there is no bulk file-import path for either table, only the single-row
`POST /ad-set-start-dates` API and the `change_log` workbook upload (`changelog_ad_set`/
`changelog_ad` sheets, detected by sheet name via `is_change_log_workbook`).
