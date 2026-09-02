"""Coverage for backend/security.py and the startup gate guard.

These exercise the request-hardening layer in isolation -- the rate limiter, the brute-force
throttle, the CSP builder's script hashing, the response headers, and the fail-closed startup
check -- plus the preview-token validation that stops glob smuggling.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import auth, core, security


class SlidingWindowTests(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        window = security._SlidingWindow()
        results = [window.hit("ip", limit=3, window=60) for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])

    def test_zero_limit_is_unlimited(self):
        window = security._SlidingWindow()
        self.assertTrue(all(window.hit("ip", limit=0, window=60) for _ in range(100)))

    def test_keys_are_independent(self):
        window = security._SlidingWindow()
        window.hit("a", limit=1, window=60)
        self.assertTrue(window.hit("b", limit=1, window=60))


class BruteForceTests(unittest.TestCase):
    def setUp(self):
        security._auth_fail = security._SlidingWindow()
        self._limit = security.AUTH_FAIL_LIMIT
        security.AUTH_FAIL_LIMIT = 3

    def tearDown(self):
        security.AUTH_FAIL_LIMIT = self._limit
        security._auth_fail = security._SlidingWindow()

    def test_blocks_after_the_failure_budget(self):
        ip = "1.2.3.4"
        self.assertFalse(security.auth_blocked(ip))
        for _ in range(3):
            security.record_auth_failure(ip)
        self.assertTrue(security.auth_blocked(ip))

    def test_one_ip_cannot_lock_out_another(self):
        for _ in range(3):
            security.record_auth_failure("attacker")
        self.assertTrue(security.auth_blocked("attacker"))
        self.assertFalse(security.auth_blocked("victim"))

    def test_successful_sign_in_can_clear_lockout(self):
        ip = "1.2.3.4"
        for _ in range(3):
            security.record_auth_failure(ip)
        self.assertTrue(security.auth_blocked(ip))
        security.clear_auth_failures(ip)
        self.assertFalse(security.auth_blocked(ip))


class ClientIpTests(unittest.TestCase):
    """The limiter key has to be the one hop a client cannot choose for itself.

    Reading the left-most X-Forwarded-For entry (which is what this did until 2026-08-20) meant
    an attacker could reset every counter -- general limit and Basic Auth lockout alike -- by
    varying a header they control, so the brute-force throttle protected nothing against anyone
    who thought to send one.
    """

    def _request(self, forwarded: str | None, peer: str = "10.0.0.1"):
        headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=peer))

    def test_uses_the_hop_the_trusted_proxy_appended(self):
        # "9.9.9.9" is whatever the client sent; "10.0.0.1" is what our proxy actually saw.
        self.assertEqual(security.client_ip(self._request("9.9.9.9, 10.0.0.1")), "10.0.0.1")

    def test_forged_left_most_hop_cannot_shift_the_key(self):
        keys = {
            security.client_ip(self._request(f"{n}.{n}.{n}.{n}, 10.0.0.1"))
            for n in range(1, 20)
        }
        self.assertEqual(keys, {"10.0.0.1"})

    def test_single_hop_header_is_used_as_is(self):
        self.assertEqual(security.client_ip(self._request("10.0.0.1")), "10.0.0.1")

    def test_fewer_hops_than_configured_does_not_raise(self):
        self.assertEqual(security.client_ip(self._request("")), "10.0.0.1")

    def test_falls_back_to_socket_peer(self):
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
        self.assertEqual(security.client_ip(request), "127.0.0.1")


class SlidingWindowMemoryTests(unittest.TestCase):
    """The limiter must not become the memory-exhaustion vector it exists to prevent."""

    def test_key_table_stays_bounded(self):
        original = security.MAX_TRACKED_KEYS
        security.MAX_TRACKED_KEYS = 50
        try:
            window = security._SlidingWindow()
            for n in range(500):
                window.hit(f"ip-{n}", limit=5, window=60)
            self.assertLessEqual(len(window._events), 50)
        finally:
            security.MAX_TRACKED_KEYS = original

    def test_per_key_history_stays_bounded_under_a_flood(self):
        window = security._SlidingWindow()
        for _ in range(1000):
            window.hit("flooder", limit=3, window=60)
        self.assertLessEqual(len(window._events["flooder"]), 4)
        self.assertFalse(window.hit("flooder", limit=3, window=60))


class CspTests(unittest.TestCase):
    def test_inline_script_is_hashed_not_unsafe_inline(self):
        index = Path(__file__).resolve().parents[1] / "frontend" / "index.html"
        csp = security.build_csp(index)
        script_src = csp.split("script-src ")[1].split(";")[0]
        self.assertIn("'sha256-", script_src)          # the theme script is allowed by hash
        self.assertNotIn("'unsafe-inline'", script_src)  # ...and only by hash
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)

    def test_missing_shell_still_yields_a_policy(self):
        csp = security.build_csp(Path("/does/not/exist.html"))
        self.assertIn("script-src 'self'", csp)


class HeaderTests(unittest.TestCase):
    def setUp(self):
        security.configure_csp(Path(__file__).resolve().parents[1] / "frontend" / "index.html")

    def _headers(self, is_https: bool) -> dict:
        # A plain dict's setdefault matches the semantics apply_headers relies on (only sets a
        # header if absent), which is all this check needs.
        response = SimpleNamespace(headers={})
        security.apply_headers(response, is_https)
        return response.headers

    def test_core_headers_present(self):
        headers = self._headers(is_https=False)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("Content-Security-Policy", headers)
        self.assertNotIn("Strict-Transport-Security", headers)  # not over TLS

    def test_hsts_only_over_https(self):
        headers = self._headers(is_https=True)
        self.assertIn("Strict-Transport-Security", headers)


class StartupGateTests(unittest.TestCase):
    """require_gate_or_die is the fail-closed boot check for public deployments."""

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("RENDER", "LEADLENS_REQUIRE_AUTH")}
        os.environ.pop("RENDER", None)
        os.environ.pop("LEADLENS_REQUIRE_AUTH", None)
        self._mode = (auth.config.team_domain, auth.config.aud, auth.config.tailscale,
                      auth.config.basic_user, auth.config.basic_pass)
        auth.config.team_domain = auth.config.aud = ""
        auth.config.tailscale = False
        auth.config.basic_user = auth.config.basic_pass = ""

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        (auth.config.team_domain, auth.config.aud, auth.config.tailscale,
         auth.config.basic_user, auth.config.basic_pass) = self._mode

    def test_open_local_boot_is_allowed(self):
        auth.require_gate_or_die()  # no marker, no gate -> fine

    def test_render_without_a_gate_refuses_to_boot(self):
        # Previously this generated a password and logged it in clear text. Failing closed is
        # the whole point of the function; see the comment in require_gate_or_die.
        os.environ["RENDER"] = "true"
        with self.assertRaises(RuntimeError):
            auth.require_gate_or_die()
        self.assertEqual(auth.config.mode, "")

    def test_no_generated_credential_is_ever_logged(self):
        os.environ["RENDER"] = "true"
        with self.assertLogs("leadlens.auth", level="DEBUG") as captured:
            logging.getLogger("leadlens.auth").debug("probe")  # assertLogs needs >=1 record
            with self.assertRaises(RuntimeError):
                auth.require_gate_or_die()
        self.assertNotIn("password=", " ".join(captured.output).lower())

    def test_render_with_basic_auth_boots(self):
        os.environ["RENDER"] = "true"
        auth.config.basic_user, auth.config.basic_pass = "demo", "secret"
        auth.require_gate_or_die()  # gate present -> no raise

    def test_basic_auth_allows_the_react_shell_to_load(self):
        auth.config.basic_user, auth.config.basic_pass = "demo", "secret"
        request = SimpleNamespace(url=SimpleNamespace(path="/"), method="GET")
        self.assertTrue(auth.is_exempt_request(request))

    def test_basic_auth_keeps_api_routes_protected(self):
        auth.config.basic_user, auth.config.basic_pass = "demo", "secret"
        request = SimpleNamespace(url=SimpleNamespace(path="/api/dashboard/summary"), method="GET")
        self.assertFalse(auth.is_exempt_request(request))


class PreviewTokenTests(unittest.TestCase):
    def test_non_hex_token_is_refused_before_glob(self):
        # A glob metacharacter must never reach PREVIEW_DIR.glob; it is rejected as invalid.
        for bad in ("*", "../etc", "abc", "", "A" * 32, "g" * 32):
            with self.assertRaises(ValueError):
                core.import_preview(bad)


class CsvExportSafetyTests(unittest.TestCase):
    """Exported cells must not become formulas when the download is opened in a spreadsheet."""

    def test_formula_leads_are_neutralised(self):
        from backend.app import _csv_safe
        for payload in ("=1+1", "+1", "-1", "@SUM(A1)", chr(9) + "=1", chr(13) + "=1"):
            with self.subTest(payload=payload):
                self.assertEqual(_csv_safe(payload), "'" + payload)

    def test_ordinary_text_is_untouched(self):
        from backend.app import _csv_safe
        for payload in ("Explorer by SL", "Campaign 2026", "", "a=b"):
            self.assertEqual(_csv_safe(payload), payload)

    def test_numbers_pass_through_unquoted(self):
        # Negative numbers must stay numeric -- only *strings* are prefixed.
        from backend.app import _csv_safe
        for payload in (5, -5, 0.5, None, True):
            self.assertEqual(_csv_safe(payload), payload)


class PreviewRetentionTests(unittest.TestCase):
    """Uploaded previews are raw customer workbooks; they must not live on disk forever."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._original = core.PREVIEW_DIR
        core.PREVIEW_DIR = Path(self._dir)

    def tearDown(self):
        core.PREVIEW_DIR = self._original
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_stale_previews_are_purged_and_fresh_ones_kept(self):
        stale = core.PREVIEW_DIR / ("a" * 32 + ".csv")
        fresh = core.PREVIEW_DIR / ("b" * 32 + ".csv")
        stale.write_text("Customer Name,Someone Real", encoding="utf-8")
        fresh.write_text("Customer Name,Someone Else", encoding="utf-8")
        old = time.time() - (core.PREVIEW_TTL_SECONDS + 60)
        os.utime(stale, (old, old))

        removed = core.purge_stale_previews()

        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_zero_ttl_disables_the_sweep(self):
        keep = core.PREVIEW_DIR / ("c" * 32 + ".csv")
        keep.write_text("x", encoding="utf-8")
        os.utime(keep, (0, 0))
        self.assertEqual(core.purge_stale_previews(ttl_seconds=0), 0)
        self.assertTrue(keep.exists())

    def test_unsupported_extension_is_refused_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            core.preview_file(b"whatever", "payload.exe")
        with self.assertRaises(ValueError):
            core.preview_file(b"whatever", "no-extension")
        self.assertEqual(list(core.PREVIEW_DIR.iterdir()), [])


class SessionTests(unittest.TestCase):
    """Sign-in sessions replaced the stored Basic credential on 2026-08-20.

    The property that matters is that the browser now holds something *revocable* that is not
    the password. These check the parts that make that true: the token is opaque, only its hash
    is stored, and it stops working the moment the account behind it stops being valid.
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._db = Path(self._dir) / "sessions.db"
        self._original_env = os.environ.get("LEADLENS_DB_PATH")
        os.environ["LEADLENS_DB_PATH"] = str(self._db)
        self._original_core = core.DB_PATH
        core.DB_PATH = self._db
        core.init_db()

    def tearDown(self):
        core.DB_PATH = self._original_core
        if self._original_env is None:
            os.environ.pop("LEADLENS_DB_PATH", None)
        else:
            os.environ["LEADLENS_DB_PATH"] = self._original_env
        shutil.rmtree(self._dir, ignore_errors=True)

    def _make_user(self, email="staff@example.com", role="staff", status="active"):
        return auth.save_user(
            {"email": email, "full_name": "Test User", "role": role,
             "status": status, "password": "a-password-123"},
            actor="admin@example.com",
        )

    def test_token_is_opaque_and_only_its_hash_is_stored(self):
        self._make_user()
        token = auth.create_session("staff@example.com")["token"]
        self.assertNotIn("a-password-123", token)
        with sqlite3.connect(self._db) as db:
            stored = db.execute("SELECT token_hash FROM app_sessions").fetchone()[0]
        self.assertNotEqual(stored, token)
        self.assertNotIn(token, stored)
        self.assertEqual(len(stored), 64)  # sha256 hex

    def test_session_resolves_to_the_live_role_not_a_snapshot(self):
        user = self._make_user(role="staff")
        token = auth.create_session("staff@example.com")["token"]
        self.assertEqual(auth.resolve_session(token)[1], "staff")
        auth.save_user({"email": "staff@example.com", "full_name": "Test User",
                        "role": "manager", "status": "active"},
                       actor="admin@example.com", user_id=user["id"])
        # Same token, new role -- a promotion or demotion must not wait for re-login.
        self.assertEqual(auth.resolve_session(token)[1], "manager")

    def test_disabling_an_account_kills_its_live_sessions(self):
        user = self._make_user()
        token = auth.create_session("staff@example.com")["token"]
        self.assertIsNotNone(auth.resolve_session(token))
        auth.save_user({"email": "staff@example.com", "full_name": "Test User",
                        "role": "staff", "status": "disabled"},
                       actor="admin@example.com", user_id=user["id"])
        self.assertIsNone(auth.resolve_session(token))

    def test_deleting_an_account_kills_its_live_sessions(self):
        user = self._make_user()
        token = auth.create_session("staff@example.com")["token"]
        auth.delete_user(user["id"], actor="admin@example.com")
        self.assertIsNone(auth.resolve_session(token))

    def test_logout_revokes_only_that_session(self):
        self._make_user()
        first = auth.create_session("staff@example.com")["token"]
        second = auth.create_session("staff@example.com")["token"]
        auth.revoke_session(first)
        self.assertIsNone(auth.resolve_session(first))
        self.assertIsNotNone(auth.resolve_session(second))

    def test_expired_session_is_refused_and_cleaned_up(self):
        self._make_user()
        token = auth.create_session("staff@example.com")["token"]
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="microseconds")
        with sqlite3.connect(self._db) as db:
            db.execute("UPDATE app_sessions SET expires_at=?", (past,))
        self.assertIsNone(auth.resolve_session(token))
        with sqlite3.connect(self._db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM app_sessions").fetchone()[0], 0)

    def test_unknown_and_empty_tokens_are_refused(self):
        for bad in ("", "not-a-token", "x" * 43):
            self.assertIsNone(auth.resolve_session(bad))

    def test_signing_in_is_not_treated_as_a_write(self):
        # A read-only account must be able to POST /api/auth/login. Guarding it with the
        # writer check would 403 every staff user out of the dashboard entirely.
        request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/auth/login"))
        self.assertFalse(auth._is_write(request))
        request = SimpleNamespace(method="DELETE", url=SimpleNamespace(path="/api/leads/1"))
        self.assertTrue(auth._is_write(request))

    def test_staff_write_scope_is_limited_to_lead_quality_routes(self):
        lead_patch = SimpleNamespace(method="PATCH", url=SimpleNamespace(path="/api/leads/1"))
        bulk_quality = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/leads/bulk-quality"))
        bulk_delete = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/leads/bulk-delete"))
        create_lead = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/leads"))
        delete_lead = SimpleNamespace(method="DELETE", url=SimpleNamespace(path="/api/leads/1"))
        upload = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/uploads/confirm"))

        self.assertTrue(auth._role_may_write_request("staff", lead_patch))
        self.assertTrue(auth._role_may_write_request("staff", bulk_quality))
        self.assertFalse(auth._role_may_write_request("staff", bulk_delete))
        self.assertFalse(auth._role_may_write_request("staff", create_lead))
        self.assertFalse(auth._role_may_write_request("staff", delete_lead))
        self.assertFalse(auth._role_may_write_request("staff", upload))
        self.assertTrue(auth._role_may_write_request("manager", upload))
        self.assertTrue(auth._role_may_write_request("admin", delete_lead))

    def test_staff_cannot_patch_lead_status(self):
        from fastapi import HTTPException
        from backend.app import LeadUpdate, patch_lead

        request = SimpleNamespace(state=SimpleNamespace(user_role="staff"))
        with self.assertRaises(HTTPException) as raised:
            patch_lead(1, LeadUpdate(status="Existing"), request)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "Staff can only change lead quality.")


if __name__ == "__main__":
    unittest.main()
