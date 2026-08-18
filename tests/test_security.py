"""Coverage for backend/security.py and the startup gate guard.

These exercise the request-hardening layer in isolation -- the rate limiter, the brute-force
throttle, the CSP builder's script hashing, the response headers, and the fail-closed startup
check -- plus the preview-token validation that stops glob smuggling.
"""

from __future__ import annotations

import os
import sys
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
    def test_prefers_left_most_forwarded_hop(self):
        request = SimpleNamespace(headers={"x-forwarded-for": "9.9.9.9, 10.0.0.1"},
                                  client=SimpleNamespace(host="10.0.0.1"))
        self.assertEqual(security.client_ip(request), "9.9.9.9")

    def test_falls_back_to_socket_peer(self):
        request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
        self.assertEqual(security.client_ip(request), "127.0.0.1")


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

    def test_render_without_a_gate_generates_temporary_basic_auth(self):
        os.environ["RENDER"] = "true"
        auth.require_gate_or_die()
        self.assertEqual(auth.config.mode, "basic")
        self.assertEqual(auth.config.basic_user, "admin")
        self.assertTrue(auth.config.basic_pass)

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


if __name__ == "__main__":
    unittest.main()
