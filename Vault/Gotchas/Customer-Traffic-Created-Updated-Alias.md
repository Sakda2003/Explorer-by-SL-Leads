# Customer traffic uploads rejected when headers are "Created"/"Updated"

`detect_upload_type_from_columns` in `backend/core.py` scores a file's raw column headers
against `SOURCE_REQUIRED_COLUMNS` (`Created At`, `Customer Name`, `UTM Ad Set ID`) using
`_header_key`, which does exact normalized matching — no aliasing. Some traffic exports
(seen 2026-08-15, a trimmed/partial pull) ship `Created`/`Updated` instead of
`Created At`/`Updated At`. `read_tabular`'s renaming step already knew how to alias headers
via `KNOWN_HEADERS`, but the detector ran *before* that step and never consulted it — so a
file the importer could fully handle was rejected upfront with "File type could not be
detected," before ever reaching the lenient part of the pipeline.

Fixed by:
1. Adding `Created` → `Created At` and `Updated` → `Updated At` to `KNOWN_HEADERS` (same
   alias pattern already used for `AD_KNOWN_HEADERS`'s "Combined-Ad-Set-Dataset" aliases).
2. Making `detect_upload_type_from_columns` canonicalize each column through `KNOWN_HEADERS`
   before computing `_header_key`, so aliased headers count toward detection instead of only
   toward the later renaming.

Separately, local backend startup was failing outright (`ModuleNotFoundError: No module
named 'jwt'`) because `pyjwt[crypto]` from `requirements.txt` wasn't installed in this
environment — that's what produced the "Failed to fetch" on the Upload page (no server was
listening on :8000 at all, not an import-logic bug). `python -m pip install "pyjwt[crypto]==2.10.1"`
resolved it; worth checking `pip show pyjwt` first if uploads ever fail with the browser's
generic "Failed to fetch" rather than a 422 with a real message.
