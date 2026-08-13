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

## Third fix, 2026-08-13: `Campaign name` without `Campaign ID`

`Ad-Set-Performance-and-Traffic-*.xlsx` (the current weekly export shape) is a **two-sheet**
workbook: sheet 1 `Ad Set Performance` at ad-set×day grain, sheet 2 `Traffic` at lead grain.
`_read_raw_frame` reads sheet 0 unless a `Corrected Traffic` sheet exists, so **uploading it
imports the spend sheet only** — which is usually what you want, since `lead_events` is fed by
the model_dataset/traffic path.

It carries `Campaign name` but **no `Campaign ID`**, and that was unimportable:
`detect_upload_type_from_columns` required all of `Campaign ID`/`Ad set ID`/`Day`, and
`AD_PERFORMANCE_REQUIRED` listed `Campaign ID` too. So the file was rejected at detection —
*before* `_repair_ad_performance_attribution`, whose entire job is reconstructing that ID from
the campaign name, ever ran. That repair was only ever reachable for files where the column
existed but held blanks.

Both gates now accept `Campaign name` as a substitute. This loosens what is **accepted**, not
what is **stored**: rows whose campaign cannot be resolved still fail the
`Campaign ID != ''` check, and if that empties the frame the read raises rather than importing
unattributed spend. `CampaignNameOnlyExportTests` covers both directions, including that a file
with no campaign column at all still fails detection.

Note `_repair_ad_performance_attribution` resolves names against **`lead_events`**
(`_historical_campaign_ad_set_options`), not `daily_ad_performance` — so a brand-new campaign
with no leads yet cannot be recovered this way.

**`leads` in `daily_ad_performance` is unpopulated and always has been** — all 879 pre-existing
rows had it NULL. Lead counts come from `lead_events` / `daily_ad_set_aggregates`, so this
export having no `Leads` column costs nothing. Don't "fix" it by backfilling that column.

**Imported 2026-08-13** (`Ad-Set-Performance-and-Traffic-2026-08-09.xlsx`, days 2026-08-01→08):
128 inserted / 0 updated / 0 rejected / 0 unresolved, all 128 campaign IDs recovered, 16 budget
periods written, 0 budget conflicts. `daily_ad_performance` 879 → **1007 rows**, coverage now
2026-06-06 → **2026-08-08**, total spend $4,114.31 → **$4,723.50** (+$609.19). Reach/Frequency
non-null on every new row (no `Ad ID` column, so the rollup had nothing to collapse).
`lead_events` unchanged at 3,469. Import auto-triggered training run 310 (60 forecasts).

**Still outstanding:** the `Traffic` sheet holds 6,341 lead rows back to **2026-01-26**, while
`lead_events` starts 2026-06-06. Importing it would extend history by ~5 months and change what
the models train on — a deliberate decision, not a routine upload. Left undone.
