# Model Dataset Upload Type

Built 2026-08-03. `MODEL_DATASET_TYPE = "model_dataset"` in `backend/core.py` accepts a
workbook with a `model_dataset` sheet at **lead grain** (`created_at`, `customer_name`,
`ad_set_id` required). Reference file:
`Dataset/Datsaa/Dataset Template/Dataset 101.xlsx`, 3,038 rows × 32 columns.

One upload writes three things: `lead_events` (rows with no `ad_set_id` are skipped,
not guessed), `daily_ad_performance` (collapsed off the repeated per-lead context), and
`change_events` when the type columns are filled.

**Non-obvious decisions:**
- **Reuses the existing cleaners.** Each half is rendered back into the CRM / Meta
  export shape and passed through `read_tabular` / `read_ad_performance_tabular`, so
  model datasets get the same ID protection and attribution repair. Don't re-implement
  cleaning here.
- **Model dataset outranks change log in detection** — the template carries both sheet
  sets, and the lead rows can't be recovered from anywhere else.
- **The ad-set-day form of the sheet is rejected.** It has a `leads` count and no lead
  identity; importing it would invent nameless leads.
- **Change types state the ad set's state per day, not an event.** An event is recorded
  on the first day a state appears and each day it changes.
- **Values typed into this sheet are `confirmed`**, unlike the template's seeded
  guesses. See [[Change-Log-Importer]].

**Solved 2026-08-03 by the optional `ad_set_days` sheet.** A lead-grain sheet alone can
only describe days that produced a lead — 161 of 879 ad-set-days spent and got nothing
(12% of spend). When `ad_set_days` is present it **replaces** the collapsed lead
context entirely and spend reconciles to the Meta export total; when absent the
preview warns.

**First real (non-template) use, 2026-08-06.** Uploaded four "Full Information
Dataset" files (`Dataset 06-06 to 07-10.xlsx`, `07-11 to 07-17`, `07-18 to 07-24`,
`07-25 to 08-01`) covering the same date ranges as the existing `Combined-Ad-Set-Dataset`
Ad spend uploads — the two upload types coexist (Data History: 8 confirmed files, one
Model dataset + one Ad spend row per week). Each carries 5 of the 10 declared
variables with real values (spend, holiday proximity, days_since_adset_started,
frequency, day-of-week) plus the target (leads); `ad_set_change_type` and
`ad_change_type` columns were present but empty, so the preview/confirm warned
"variables 6, 7, 9 and 10 will keep using the system's inferred events" and
`change_events_inserted` was 0 for all four — this did **not** close the
confirmed-vs-inferred gap noted in [[Dataset-Page]]/[[Change-Log-Importer]], only
variables 2/3/4/5/8. Each confirm triggered an automatic retrain (`training_run`
ids 202-205). Uploaded via direct API calls (`/api/uploads/preview` →
`/api/uploads/confirm`) rather than the browser file picker, since the harness's
browser-automation tools have no file-upload action for this app's drag-and-drop
input.
