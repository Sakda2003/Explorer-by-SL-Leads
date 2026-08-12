# Model Dataset Template

`build_model_template.py` → `Dataset/MODEL_DATASET_TEMPLATE.xlsx` (built 2026-08-03).
Six sheets: README, model_dataset (879 rows, all 10 declared variables, AUTO),
changelog_ad_set, changelog_ad, holiday_calendar, data_dictionary.

It reads events out of `Dataset/master_dataset.xlsx` rather than re-running change
detection — the thresholds live in `build_master_dataset.py` and `backend/core.py`,
and a third copy would drift. See [[Master-Dataset]].

Only **variables 3, 6, 7, 9, 10** are worth hand-recording. Variables 1, 2, 4, 5, 8
come straight off the exports and the calendar and cannot be improved by typing. Every
seeded changelog row carries `source=inferred`; the workflow is for the user to
confirm, correct, or delete each one, and the `*_source` columns propagate that into
the model dataset.

**Wired to ingest** as of 2026-08-03 — see [[Change-Log-Importer]]. Uploading the
workbook imports its changelog sheets; only `source='confirmed'` rows affect the model.

**Why:** the ask was "upload this and the forecast gets accurate" — the template alone
changes nothing until ingest consumes it, and even then it only attacks the ~12% of
daily error that isn't irreducible noise. See [[Forecast-Flatness-Is-The-Data]].

**How to apply:** when a filled template comes back, the first job is the importer.
The honest framing: confirmed change events remove the
`targeting_change`-detected-from-`frequency` circularity — that's the mechanism, not
"more columns = better fit."
