# Change Log Importer

Built 2026-08-03. Uploading `MODEL_DATASET_TEMPLATE.xlsx` imports its
`changelog_ad_set` / `changelog_ad` sheets into a `change_events` table
(`CHANGE_LOG_TYPE = "change_log"`, `backend/core.py`).

**Two rules that are easy to break:**
1. **Only `source='confirmed'` rows reach the model.** Rows still marked `inferred` /
   `not_recorded` are the detector's own guess coming back out of the template —
   storing them as fact would launder a guess into a recorded event. Kept for the
   audit trail, excluded from every feature.
2. **Override is per ad set, all-or-nothing.** Any confirmed events for an ad set makes
   the feature builders drop the detector entirely for that ad set. Mixing recorded and
   detected events would put two different definitions of "a change" in one column. Ad
   sets with no confirmed events keep inferring.

Detection is identified by **sheet name**, not columns — the template's first sheet is
a README, so the column-based type detector never sees the change log.

**Why:** this is the mechanism that fixes the circularity in [[Master-Dataset]] —
`targeting_change` was detected *from* frequency and then used alongside frequency as
a separate regressor.

**How to apply:** see [[Model-Dataset-Template]] for the workbook itself. Expected
gain is bounded — [[Forecast-Flatness-Is-The-Data]] measured ~88% of daily error as
irreducible, so judge this at weekly/portfolio grain. Also feeds
[[Change-Event-UI-Recorder]], which writes into the same table.
