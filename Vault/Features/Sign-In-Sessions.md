# Sign-in sessions

Built 2026-08-20, replacing the stored Basic Auth credential. Part of the sweep in
[[Security-Audit-2026-08-20]]; the auth topologies it sits inside are in [[Access-Control]].

## What it replaced, and why that mattered

The dashboard used to sign in by building `Authorization: Basic base64(user:pass)` and putting
**that string** in `localStorage` for 30 days (`AUTH_STORAGE_KEY = 'leadlens-basic-auth'`,
added by the "Persist login for thirty days" commit). Two consequences:

- **It was the password, not a session.** Base64 is encoding, not encryption — one `atob()`
  recovers the plaintext. Anything that could read `localStorage` got the credential itself,
  usable anywhere, forever.
- **It could not be revoked.** Disabling an account in the admin screen did nothing to a
  browser already holding the credential; only changing the password would, and that logs
  everyone out.

The residual XSS risk was genuinely low — the CSP hash-pins scripts (see
[[CSP-Inline-Script-Hash]]), nothing renders user-supplied HTML, and there are no
`dangerouslySetInnerHTML` sinks. The problem was the blast radius if that ever stopped being
true, plus having no revocation story at all.

## How it works now

`app_sessions(token_hash, email, created_at, expires_at, last_seen_at)` in `backend/core.py`.

- **Sign-in** (`POST /api/auth/login`) is authenticated by the middleware like any other
  route, so the endpoint itself never sees or re-checks a password — it just mints a token for
  the already-established `request.state.user_email`. `secrets.token_urlsafe(32)`, returned
  once.
- **Only `sha256(token)` is stored.** The token is high-entropy random, not a password, so a
  single unsalted hash is the correct construction — there is nothing to brute-force — and a
  leaked copy of the database yields no usable sessions.
- **The browser sends `Authorization: Bearer <token>`.** `Basic` is still accepted, so curl,
  the container healthcheck, and any non-browser client keep working.
- **Sign-out (`POST /api/auth/logout`) revokes server-side.** Clearing `localStorage` alone
  would leave a captured token live.

## Two decisions worth not undoing

**The session proves *who*, never *what they may do*.** `resolve_session()` re-reads the role
from `app_users` on every request instead of baking it into the token at sign-in. That is what
makes a demotion take effect immediately rather than at the session's natural expiry.
Verified both directions: a staff user promoted to manager can write on the *same* token
without re-login, and a disabled account's live session dies on the next request.

**`/api/auth/login` and `/api/auth/logout` are exempt from the writer-role check**
(`_SESSION_PATHS` / `_is_write` in `auth.py`). They are POSTs, and `_WRITE_METHODS` contains
POST, so without the exemption the writer check would **403 every read-only account out of
signing in at all** — a staff user could never reach the dashboard. This was caught in
integration testing, not review; if you refactor the write check, keep the exemption.

## Migration

Existing browsers held the old `Basic ...` value. On first load `readLegacyCredential()` finds
it, spends it once against `/auth/login`, and replaces it with a session. **Nobody has to
re-type a password, and — the actual point — the stored password is erased on first page
load** rather than lingering until someone happens to sign out. Verified in the browser:
seeded a legacy entry, reloaded, stayed signed in, and `localStorage` came back holding a
43-character opaque token with no trace of the password.

## Verified end-to-end (2026-08-20)

In a real browser against a gated instance, not just unit tests:

| Check | Result |
|---|---|
| After sign-in, `localStorage` contains the password | no — 43-char opaque token |
| Requests carry `Bearer` and succeed | `/auth/login` 200, all subsequent calls 200 |
| Captured token replayed after sign-out | **401** (worked, 200, immediately before) |
| Legacy `Basic` entry on reload | exchanged silently; password erased |
| Read-only account can sign in | yes, and its writes are still 403 |
| Console errors | none |

Plus 8 unit tests in `tests/test_security.py::SessionTests`.

`LEADLENS_SESSION_TTL_DAYS` (default 30) sets the lifetime. Expired rows are deleted lazily on
the next sign-in and on any attempt to use one.
