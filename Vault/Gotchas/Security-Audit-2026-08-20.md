# Security audit, 2026-08-20

A full-codebase sweep for security risks and dead weight. Eight fixes landed; three items were
deliberately left for Sakda to decide. Everything below is on the `security-hardening` branch,
**not yet committed or deployed**.

Companion notes: [[X-Forwarded-For-Trust-Direction]] (the most serious finding, written up
separately because the lesson generalises), [[Access-Control]] (the layer this all sits in).

## What was already fine

Worth recording so the next audit doesn't re-derive it:

- **No SQL injection anywhere.** Every dynamic fragment in `core.py` (`_dataset_where`,
  `get_dataset_rows`, `get_dataset_row_ids`) resolves table names, columns, sort keys and
  filter fields through the `DATASET_ROW_TABLES` allowlist; only values are bound, always
  via `?`. The `ALTER TABLE` f-strings are hardcoded migration constants.
- **No secrets in git history.** `.env` and `backup.env` were never added; no `.db`, `.csv` or
  `.xlsx` has ever been committed. `.gitignore` and `.dockerignore` both cover them.
- **Backup crypto is sound.** AES-256-GCM with scrypt KDF, per-backup random salt and nonce,
  MAGIC as AAD, 16-char minimum passphrase.
- **No `eval`/`exec`/`pickle`/`shell=True`, no `verify=False`, no XSS sinks in the frontend.**

## Fixed

1. **`X-Forwarded-For` spoofing defeated every rate limit and the auth lockout.** See
   [[X-Forwarded-For-Trust-Direction]].
2. **The rate limiter's own memory was unbounded** — the eviction branch was unreachable. Same
   note.
3. **PBKDF2 amplification on the request path.** Basic Auth resends the password on every
   request, and `_verify_basic` called `ensure_basic_user()` each time, which computed
   `_hash_password()` *unconditionally* — before checking whether the row needed updating at
   all. So every authenticated request paid **two** 260,000-iteration key derivations, roughly
   a quarter-second of CPU on the single worker, drivable by anyone who could send an
   `Authorization` header. Now: `ensure_basic_user()` caches per credential pair and only
   hashes when it actually writes, plus a 60-second `_VerifiedCredentials` TTL cache keyed by a
   **salted blake2b digest** of the credential (never the credential itself). Failures are
   never cached — guessing stays full price, and is separately bounded by the now-working
   lockout. Any write to `app_users` clears both caches, so disabling an account takes effect
   promptly rather than at the TTL.
4. **The Render branch of `require_gate_or_die()` was a fail-*open* dressed as a fail-closed.**
   Booting on Render with no gate minted a random password and **logged it in clear text**.
   That put a live credential into Render's retained, searchable, account-wide log stream, and
   it meant the one deployment style the function was written in response to (the open Render
   deploy of 2026-08-14) was the only one it did not stop. Removed; Render now fails closed
   like every other host. `render.yaml` already sets `BASIC_AUTH_PASS` via
   `generateValue: true`, so the blueprint path was never relying on it.
5. **Uploaded previews accumulated forever.** `data/previews/` held **56 files / 17 MB** — raw
   uploaded workbooks, i.e. lead-grain files carrying `customer_name` and the rest of the PII,
   sitting outside the database with none of the database's access control in front of them.
   Nothing ever deleted them, imported or not. Now: `import_preview` deletes its own file in a
   `finally` (the importers have already copied what they need), and `purge_stale_previews()`
   ages out abandoned ones after `LEADLENS_PREVIEW_TTL_HOURS` (default 24), swept on startup
   and on each new upload. **The existing 56 files clear themselves on the next app start** —
   no manual step needed. `preview_file` also now refuses any extension outside
   `ALLOWED_UPLOAD_SUFFIXES`, since it derived its on-disk filename from the caller's.
6. **CSV formula injection in all five exports.** Campaign and ad-set names come straight out
   of a Meta workbook; `full_name` comes from the admin screen. A cell beginning `=`, `+`, `-`,
   `@`, tab or CR is executed as a formula by Excel/Sheets/LibreOffice, so a download was a
   live code path on the opener's machine. `_write_csv_row` now runs every string cell through
   `_csv_safe`, which prefixes an apostrophe. **Numbers are untouched** — only `str` values are
   prefixed, so negative figures stay numeric.
7. **The test suite ran against the live production database.** `tests/test_pipeline.py` and
   `test_backup.py` build their own temp directories, but `test_auth.py` did not — with
   `LEADLENS_DATA_DIR` unset, `auth._db_path()` resolves to the real `data/leadlens.db`, and
   `_verify_basic` *writes* to it (`last_login_at` plus an audit row) on the `/api/auth/me`
   path. Found the hard way: an ad-hoc `TestClient` check during this audit, run with
   `BASIC_AUTH_USER=demo`, made `ensure_basic_user()` create a live, active **admin account in
   the production database**. It did precisely what it is designed to do, against the wrong
   file. The row was deleted by hand (`app_users` back to 0 rows, `lead_events` intact at
   3,801), and it was only noticed because it then broke an unrelated assertion. New
   `tests/conftest.py` points `LEADLENS_DATA_DIR`/`LEADLENS_DB_PATH` at a temp directory before
   `backend.core` is imported — conftest is the only hook early enough, since `core.DB_PATH` is
   resolved at import time — clears any leaked gate env vars, and adds a session fixture that
   asserts neither path resolves to the real database.
8. **26 CVEs across the pinned Python dependencies**, several landing on paths this app
   actually mounts. Detail in the section below.

**The mechanism behind #7 is worth its own line, because it is a live hazard beyond the tests:**
`backend/core.py` calls `init_db()` at **module scope** (line ~9903). So *importing*
`backend.core` — from a script, a REPL, a notebook, an ad-hoc `TestClient` check — runs schema
migrations, `ensure_basic_user()`, and now the preview purge against whatever `DB_PATH`
resolved to at import time. There is no way to import the module to *look* at it without
writing to a database. During this audit that turned two innocuous-looking `python -c "from
backend.app import app"` commands into real writes on `data/leadlens.db`: it created the
`app_sessions` table and swept the 56 stale preview files. Both are exactly what a normal app
start does and both were verified harmless (lead_events 3,801, forecasts 16,292,
daily_ad_performance 1,100, raw_uploads 15 — all unchanged; `app_users` and `app_sessions`
empty), but *nothing about the command said it would touch production*. `conftest.py` fixes
this for the test suite. Anything else that imports `backend.core` still needs
`LEADLENS_DB_PATH` set first.

## Dependency bumps and the pinning trap

`pip-audit` against the old `requirements.txt`: **26 advisories**. The three that matter here:

- **pyjwt 2.10.1 → 2.13.0.** PYSEC-2026-176 is a verifier-side **algorithm allow-list bypass**
  in `jwt.decode()`. `backend/auth.py` pins `algorithms=["RS256"]` *specifically* to stop
  alg-confusion forgery of the Cloudflare Access token — so this advisory landed directly on
  the app's only cryptographic check. PYSEC-2026-120 (unvalidated `crit` header) is reachable
  on the same path.
- **starlette 0.47.3 → 1.6.0.** PYSEC-2026-2281 is a `StaticFiles` UNC-path SSRF **that only
  triggers on Windows** — and the recommended deployment for this app is a Windows PC
  (`deploy/RUNBOOK-TAILSCALE-WINDOWS.md`), with `StaticFiles` mounted at `/`. Also
  PYSEC-2026-1942 (quadratic-time `Range` header DoS on `FileResponse`, unauthenticated in
  Basic Auth mode since the shell is exempt) and PYSEC-2026-161/-248 (unvalidated Host/path in
  `request.url`).
- **python-multipart 0.0.20 → 0.0.32.** Five DoS/parsing advisories on the multipart path,
  which is the file-upload route.

**The trap worth remembering: `pip install --upgrade fastapi` does not fix starlette.** FastAPI
declares `starlette>=0.46.0` with **no upper bound**, so pip happily leaves a vulnerable
0.47.x in place while reporting success. `starlette` is now pinned directly in
`requirements.txt` with a comment saying why, rather than being left as a transitive.

Also bumped: `fastapi` 0.116.1 → 0.141.1, `uvicorn` 0.35.0 → 0.52.4. After the bump
`pip-audit` reports **no known vulnerabilities**, `pnpm audit` was already clean, and all
**236 tests pass** — including an end-to-end check through `TestClient` that auth, the security
headers, the body cap and the docs-hiding all still behave under starlette 1.x.

**Why the CI workflow didn't catch this:** `.github/workflows/security-audit.yml` runs
`pip-audit --strict` on push to `main`, on PRs, and weekly — but this work has been sitting on
the `security-hardening` branch, which triggers none of those.

## Decisions (asked 2026-08-20)

- ~~The login token in `localStorage`~~ — **decided and built**: Sakda chose server-side session
  tokens. See [[Sign-In-Sessions]].
- ~~`pandas` pinned to 2.3.1 while the `.venv` ran 3.0.1~~ — **decided**: Sakda chose to align the
  pin to **3.0.1**, matching the version the 244 tests actually pass against. Worth watching on
  the next deploy: pandas 3.0 changed copy-on-write and string-dtype behaviour, and the
  forecasting pipeline had never been exercised on 2.3.1 by CI either way.
- **Left in place at Sakda's request** — recorded here so a future pass doesn't re-raise them as
  new findings. Four orphaned root scripts with no reference anywhere in the repo or Vault:
  `build_blanks_audit.py`, `build_dataset101_rebuild.py`, `build_lead_dataset.py`,
  `build_model_dataset_lead_grain.py`. Harmless, but they are one-off analysis tools whose
  moment has passed. Also `data/` holds four stale `leadlens.db.bak-*` / `.pre-aug-import`
  copies — each a full duplicate of the PII database. All gitignored, so not a leak; deleting
  them is Sakda's call.
