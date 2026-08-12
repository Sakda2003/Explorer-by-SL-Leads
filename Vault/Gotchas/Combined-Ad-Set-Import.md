# Combined-Ad-Set-Dataset Import

Sakda's `Combined-Ad-Set-Dataset *.xlsx` files (ad × day grain: `Day`, `Campaign Name/ID`,
`Ad set ID`, `Ad ID`, `Fb Ad Title`, `Reach`, `Impression`, `Frequency`, `Messaging
Conversation`, `Ad set budget[/type]`, `Amount Spent (USD)`, `Leads`, `Cost Per Lead`,
`Cost Per Message`) now import through the **existing** `read_ad_performance_tabular` /
`_write_ad_performance` path in `backend/core.py` — no new table, no new upload type.

**Split of responsibility, by design (2026-08-05):** these files are the source of truth
for `daily_ad_performance` (spend, leads, reach, frequency, CPL, budget — everything the
graphs read). The already-imported `model_dataset` lead-grain workbooks stay the source
for `lead_events` (customer name, status) shown in the Leads list. Uploading a Combined
file **upserts onto** `daily_ad_performance` on `(day, campaign_id, ad_set_id)` — it
overwrites the ad_set_days-derived rows the model_dataset import wrote there, with the
Meta-shaped numbers from this file. Nothing else in the pipeline needed to change: the
model_dataset importer never stops feeding `lead_events`, and `daily_ad_performance` was
always what the graphs and forecasts (`get_ad_spend_analytics`, master dataset, etc.)
already read from.

**Two fixes were required in `backend/core.py`, not just an upload:**
1. `AD_KNOWN_HEADERS` needed aliases — this file's headers (`Impression`, `Messaging
   Conversation`, `Cost Per Message`) don't hash to the same `_header_key` as the
   canonical Meta names (`Impressions`, `Messaging conversations started`, `Cost per
   messaging conversation started`), so without an alias table those columns were
   silently dropped as "ignored" rather than rejected — easy to miss.
2. `_rollup_ad_rows_to_ad_sets` **used to null `Reach`/`Frequency` for every file with an
   `Ad ID` column**, on the assumption that multiple ads might share an ad-set-day slot
   (reach can't be summed across ads without double-counting people). But this file has
   exactly one ad per ad-set per day — nothing to collapse — so the old rule was
   discarding real numbers for no reason. Fixed to only null Reach/Frequency for groups
   that actually contain >1 ad row that day; single-ad groups keep the real value. This
   matters beyond cosmetics: `frequency` step-shifts are how `build_master_dataset.py`
   infers `targeting_change`/`bid_change` (see [[Master-Dataset]]) — nulling it silently
   broke that signal for any single-ad-per-day export, which is the common case.

Verified 2026-08-05: all 4 files (`06-06 to 07-10`, `07-11 to 07-17`, `07-18 to 07-24`,
`07-25 to 07-31`) imported clean (0 rejected), upserted onto all 879 existing
`daily_ad_performance` rows (0 inserted, 879 updated — same grain, refreshed numbers),
Reach/Frequency null count dropped from 879 to 0, total spend $4,114.31. `lead_events`
count unchanged (3,023) confirming the lead-grain source was untouched. Forecast page
verified live post-import via the `leadlens-verify` preview server.

**How to apply:** future Combined-Ad-Set-Dataset uploads for new date ranges go through
the normal ad-performance upload path (same one Meta CSV/XLSX exports use) — no special
handling needed now that the aliases and rollup fix are in place. If a future Meta export
introduces yet another header spelling, add it to the alias block above
`AD_KNOWN_HEADERS` in `core.py` rather than a new importer.
