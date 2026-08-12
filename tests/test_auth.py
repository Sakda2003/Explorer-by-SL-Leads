"""Cloudflare Access verification tests.

These cover the failure modes that matter for a process holding customer records: forged
tokens, tokens minted for a different Access application, expired sign-ins, and the
read-only role. Each test drives backend.auth directly with a locally generated RSA key,
standing in for Cloudflare's signing key, so nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import auth

TEAM = "explorer.cloudflareaccess.com"
AUD = "test-audience-tag"
OWNER = "owner@example.com"
CEO = "ceo@example.com"
STRANGER = "someone@evil.example"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_PRIVATE_PEM = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_OTHER_PRIVATE_PEM = _other_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)


def _jwks() -> dict:
    """The public half, in the shape Cloudflare's /cdn-cgi/access/certs returns."""
    from jwt.algorithms import RSAAlgorithm
    import json

    jwk = json.loads(RSAAlgorithm.to_jwk(_key.public_key()))
    jwk.update({"kid": "test-kid", "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def _token(
    *,
    email: str = OWNER,
    aud: str = AUD,
    issuer: str | None = None,
    expires_in: int = 3600,
    signing_pem: bytes | None = None,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "email": email,
            "aud": aud,
            "iss": issuer or f"https://{TEAM}",
            "iat": now,
            "exp": now + expires_in,
        },
        signing_pem or _PRIVATE_PEM,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


def _request(token: str | None = None, method: str = "GET", path: str = "/api/dashboard/summary"):
    headers = {"Cf-Access-Jwt-Assertion": token} if token else {}
    return SimpleNamespace(
        headers=headers,
        cookies={},
        method=method,
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(),
    )


def verify(request):
    return asyncio.run(auth.verify(request))


class AccessTests(unittest.TestCase):
    def setUp(self):
        auth.config.team_domain = TEAM
        auth.config.aud = AUD
        auth.config.allowed = frozenset({OWNER, CEO})
        auth.config.writers = frozenset({OWNER})
        # Preload the key set so no test reaches the network.
        auth._jwks = auth.PyJWKSet.from_dict(_jwks())
        auth._jwks_fetched_at = time.monotonic()

    def tearDown(self):
        auth._jwks = None
        auth._jwks_fetched_at = 0.0

    def test_valid_owner_token_is_accepted(self):
        ok, status, _ = verify(_request(_token()))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_missing_token_is_rejected(self):
        ok, status, _ = verify(_request())
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_token_signed_by_another_key_is_rejected(self):
        """A forged token: right shape and claims, wrong signer."""
        forged = _token(signing_pem=_OTHER_PRIVATE_PEM)
        ok, status, _ = verify(_request(forged))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_token_for_a_different_access_app_is_rejected(self):
        """Guards against replaying a token from another app on the same Cloudflare team."""
        ok, status, _ = verify(_request(_token(aud="some-other-app")))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_token_from_a_different_issuer_is_rejected(self):
        ok, status, _ = verify(_request(_token(issuer="https://attacker.cloudflareaccess.com")))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_expired_token_is_rejected(self):
        ok, status, _ = verify(_request(_token(expires_in=-60)))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_unlisted_email_is_rejected_even_with_a_valid_signature(self):
        """Cloudflare admitted them; the app's own allowlist is the second gate."""
        ok, status, _ = verify(_request(_token(email=STRANGER)))
        self.assertFalse(ok)
        self.assertEqual(status, 403)

    def test_reader_may_read(self):
        ok, status, _ = verify(_request(_token(email=CEO)))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_reader_may_not_delete(self):
        """The CEO gets the dashboard without the ability to erase a lead by mistake."""
        ok, status, _ = verify(_request(_token(email=CEO), method="DELETE", path="/api/leads/1"))
        self.assertFalse(ok)
        self.assertEqual(status, 403)

    def test_writer_may_delete(self):
        ok, status, _ = verify(_request(_token(email=OWNER), method="DELETE", path="/api/leads/1"))
        self.assertTrue(ok)

    def test_email_matching_is_case_insensitive(self):
        ok, _, _ = verify(_request(_token(email=OWNER.upper())))
        self.assertTrue(ok)

    def test_health_stays_open_for_container_checks(self):
        ok, status, _ = verify(_request(path="/api/health"))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_cookie_is_accepted_for_browser_navigation(self):
        request = _request()
        request.cookies = {"CF_Authorization": _token()}
        ok, _, _ = verify(request)
        self.assertTrue(ok)

    def test_empty_writer_list_lets_every_reader_write(self):
        auth.config.writers = frozenset()
        ok, _, _ = verify(_request(_token(email=CEO), method="DELETE", path="/api/leads/1"))
        self.assertTrue(ok)


def _ts_request(
    login: str | None = OWNER,
    method: str = "GET",
    path: str = "/api/dashboard/summary",
):
    """A request as `tailscale serve` would deliver it."""
    headers = {"Tailscale-User-Login": login} if login else {}
    return SimpleNamespace(
        headers=headers,
        cookies={},
        method=method,
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(),
    )


class TailscaleTests(unittest.TestCase):
    """The no-domain, no-Cloudflare topology: identity arrives as a header from the proxy.

    These assert the part that actually matters in that mode -- that the reader/writer split
    survives the switch away from Cloudflare, and that a request with no identity is refused
    rather than waved through.
    """

    def setUp(self):
        auth.config.team_domain = ""
        auth.config.aud = ""
        auth.config.tailscale = True
        auth.config.allowed = frozenset({OWNER, CEO})
        auth.config.writers = frozenset({OWNER})

    def tearDown(self):
        auth.config.tailscale = False

    def test_writer_may_delete(self):
        ok, status, _ = verify(_ts_request(OWNER, method="DELETE", path="/api/leads/1"))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_reader_may_read(self):
        ok, status, _ = verify(_ts_request(CEO))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_reader_may_not_delete(self):
        """The whole reason this mode exists: three tailnet users, one of them able to delete."""
        ok, status, _ = verify(_ts_request(CEO, method="DELETE", path="/api/leads/1"))
        self.assertFalse(ok)
        self.assertEqual(status, 403)

    def test_unlisted_tailnet_user_is_rejected(self):
        """Being on the tailnet is not the same as being allowed into this app."""
        ok, status, _ = verify(_ts_request(STRANGER))
        self.assertFalse(ok)
        self.assertEqual(status, 403)

    def test_missing_header_fails_closed(self):
        """An accidentally published port must refuse anonymous callers, not serve them."""
        ok, status, _ = verify(_ts_request(None))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_blank_header_fails_closed(self):
        ok, status, _ = verify(_ts_request("   "))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_login_matching_is_case_insensitive(self):
        ok, _, _ = verify(_ts_request(OWNER.upper()))
        self.assertTrue(ok)

    def test_health_stays_open_for_container_checks(self):
        ok, status, _ = verify(_ts_request(None, path="/api/health"))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_identity_is_exposed_to_routes(self):
        request = _ts_request(OWNER)
        verify(request)
        self.assertEqual(request.state.user_email, OWNER)

    def test_cloudflare_jwt_header_is_not_honoured_in_tailscale_mode(self):
        """A CF_Authorization cookie or assertion header must not be a second way in."""
        request = _ts_request(None)
        request.headers = {"Cf-Access-Jwt-Assertion": _token()}
        request.cookies = {"CF_Authorization": _token()}
        ok, status, _ = verify(request)
        self.assertFalse(ok)
        self.assertEqual(status, 401)


class ModePrecedenceTests(unittest.TestCase):
    """Cloudflare's signature beats Tailscale's header when both are configured."""

    def setUp(self):
        auth.config.team_domain = TEAM
        auth.config.aud = AUD
        auth.config.tailscale = True
        auth.config.allowed = frozenset({OWNER, CEO})
        auth.config.writers = frozenset({OWNER})
        auth._jwks = auth.PyJWKSet.from_dict(_jwks())
        auth._jwks_fetched_at = time.monotonic()

    def tearDown(self):
        auth.config.tailscale = False
        auth._jwks = None
        auth._jwks_fetched_at = 0.0

    def test_mode_is_cloudflare(self):
        self.assertEqual(auth.config.mode, "cloudflare")

    def test_tailscale_header_alone_does_not_authenticate(self):
        """Otherwise setting one header would downgrade a Cloudflare deployment to a header."""
        ok, status, _ = verify(_ts_request(OWNER))
        self.assertFalse(ok)
        self.assertEqual(status, 401)

    def test_valid_jwt_still_works(self):
        ok, _, _ = verify(_request(_token()))
        self.assertTrue(ok)


class DisabledTests(unittest.TestCase):
    """Unconfigured means inert, so local development and the existing suite keep working."""

    def setUp(self):
        auth.config.team_domain = ""
        auth.config.aud = ""
        auth.config.tailscale = False

    def test_everything_passes_when_not_configured(self):
        ok, status, _ = verify(_request(method="DELETE", path="/api/leads/1"))
        self.assertTrue(ok)
        self.assertEqual(status, 200)

    def test_mode_is_empty(self):
        self.assertEqual(auth.config.mode, "")


if __name__ == "__main__":
    unittest.main()
