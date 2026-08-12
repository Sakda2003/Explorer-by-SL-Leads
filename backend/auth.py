"""Who is calling, and may they write. Two deployment topologies, one authorisation rule.

There are two supported ways to put this app in front of a few people, and they establish
identity very differently:

**Cloudflare Access** (`deploy/RUNBOOK.md`). Access gates the tunnel at the edge, so in the
happy path no unauthenticated request reaches this process at all. The check here is defence
in depth, for the paths where that assumption breaks: a misconfigured tunnel, a port
published by accident, another process on the same VPS, or a future `docker run -p`. Identity
is a signed JWT, verified cryptographically, so it stands on its own.

**Tailscale Serve** (`deploy/RUNBOOK-TAILSCALE.md`). The tailnet is the gate: the container
binds `127.0.0.1` only and nothing inbound is open, so reaching the port at all requires
already being on the machine or on the tailnet. `tailscale serve` proxies to that loopback
port and stamps each request with the tailnet identity of the caller, which is what lets the
reader/writer split keep working with no Cloudflare and no domain.

Be clear-eyed about what that second mode is worth. A header is not a signature: its
trustworthiness is inherited entirely from the topology described above, so it enforces
*role separation between people who are already authenticated by Tailscale* rather than
standing up to a network attacker who can reach the port directly. That is precisely the
job it is here to do -- stopping a third reader from deleting a lead by accident -- but it
means `docker-compose.tailscale.yml` binding loopback is load-bearing, not cosmetic. It
still fails closed on a missing header, so an accidentally published port does not hand the
dashboard to anonymous scanners.

Configuration is entirely environment-driven, and enforcement only switches on once it is
configured. With no configuration this module is inert, so local development and the test
suite keep working untouched -- but startup then logs a loud warning, because an
unconfigured deployment is exactly the accident this module is meant to catch.

    CF_ACCESS_TEAM_DOMAIN     e.g. "explorer.cloudflareaccess.com"
    CF_ACCESS_AUD             the Application Audience (AUD) tag from the Access app
    LEADLENS_TAILSCALE_AUTH   1 to trust Tailscale Serve's identity header instead
    LEADLENS_ALLOWED_EMAILS   comma-separated; who may read (empty = anyone the gate admits)
    LEADLENS_WRITER_EMAILS    comma-separated; who may write (empty = same as allowed)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx
import jwt
from jwt import PyJWKSet

log = logging.getLogger("leadlens.auth")

# Cloudflare signs Access tokens with RS256. Pinning the algorithm list is what stops an
# "alg" confusion attack, where a forged token claims HS256 and tricks the verifier into
# validating it with the public key as a shared secret.
_ALGORITHMS = ["RS256"]

# Uptime checks and container healthchecks must not require a login.
_EXEMPT_PATHS = frozenset({"/api/health"})

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_JWKS_TTL_SECONDS = 600

# Set by `tailscale serve` on every proxied request, and stripped from whatever the client
# sent, so it cannot be smuggled in from the far side of the proxy. Note that `tailscale
# funnel` is a different feature: funnel traffic comes from the public internet and carries
# no identity, so it arrives here with no header and is refused. That is the correct outcome
# -- funnel would put this database on the open internet.
_TAILSCALE_IDENTITY_HEADER = "Tailscale-User-Login"


def _emails(name: str) -> frozenset[str]:
    raw = os.getenv(name, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


class AccessConfig:
    """Resolved at import time so a misconfiguration surfaces at startup, not first request."""

    def __init__(self) -> None:
        self.team_domain = (os.getenv("CF_ACCESS_TEAM_DOMAIN") or "").strip().rstrip("/")
        self.aud = (os.getenv("CF_ACCESS_AUD") or "").strip()
        # Opt-in by an explicit flag, never inferred from the presence of the header. Deducing
        # "we must be behind Tailscale" from attacker-supplied input would let anyone switch
        # this mode on by sending the header.
        self.tailscale = _flag("LEADLENS_TAILSCALE_AUTH")
        self.allowed = _emails("LEADLENS_ALLOWED_EMAILS")
        self.writers = _emails("LEADLENS_WRITER_EMAILS")

    @property
    def enabled(self) -> bool:
        """True when some identity gate is in force. Kept as the single 'is this locked down'
        question so callers do not have to know which topology is deployed."""
        return bool(self.mode)

    @property
    def mode(self) -> str:
        """"cloudflare", "tailscale", or "" for inert.

        Cloudflare wins when both are configured: a verified signature is strictly stronger
        evidence than a proxy header, so if the JWT is available there is no reason to fall
        back to trusting a hop.
        """
        if self.team_domain and self.aud:
            return "cloudflare"
        if self.tailscale:
            return "tailscale"
        return ""

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"{self.issuer}/cdn-cgi/access/certs"

    def may_read(self, email: str) -> bool:
        return not self.allowed or email in self.allowed

    def may_write(self, email: str) -> bool:
        # An empty writer list means "whoever may read may also write". Setting it is how
        # you give the CEO the dashboard without the ability to delete a lead by mistake.
        if not self.writers:
            return self.may_read(email)
        return email in self.writers


config = AccessConfig()

_jwks: PyJWKSet | None = None
_jwks_fetched_at = 0.0
_jwk_lock = asyncio.Lock()


async def _fetch_jwks() -> PyJWKSet:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(config.certs_url)
        response.raise_for_status()
    return PyJWKSet.from_dict(response.json())


def _find(kid: str | None):
    if _jwks is None:
        return None
    for candidate in _jwks.keys:
        if candidate.key_id == kid:
            return candidate
    return None


async def _signing_key(token: str):
    """Return the Cloudflare signing key for this token, refreshing the cached JWKS if needed.

    Deliberately not PyJWKClient: that class fetches over urllib, which would block the single
    uvicorn worker on every cache miss, and its cache round-trip is internally inconsistent
    (the cache hands back a PyJWKSet which get_jwk_set then isinstance-checks as a dict).
    Fetching the set here keeps the I/O async and the lookup obvious.

    Cloudflare rotates these keys, so the cache is time-boxed and additionally refreshed once
    on an unknown key id rather than failing a request that a rotation would have broken.
    """
    global _jwks, _jwks_fetched_at
    kid = jwt.get_unverified_header(token).get("kid")

    async with _jwk_lock:
        stale = _jwks is None or (time.monotonic() - _jwks_fetched_at) > _JWKS_TTL_SECONDS
        if stale:
            _jwks = await _fetch_jwks()
            _jwks_fetched_at = time.monotonic()

        key = _find(kid)
        if key is None and not stale:
            _jwks = await _fetch_jwks()
            _jwks_fetched_at = time.monotonic()
            key = _find(kid)
        return key


def _token_from(request) -> str | None:
    # Cloudflare sends the assertion as a header for API calls and as a cookie for browser
    # navigations; a request that reaches the app can legitimately carry either.
    header = request.headers.get("Cf-Access-Jwt-Assertion")
    if header:
        return header.strip()
    cookie = request.cookies.get("CF_Authorization")
    return cookie.strip() if cookie else None


def _authorize(email: str, request) -> tuple[bool, int, str]:
    """Apply the allow/writer lists to an already-established identity.

    Both topologies land here, so the read/write split behaves identically whether identity
    came from a Cloudflare JWT or from Tailscale -- there is one place to reason about who can
    delete a lead.
    """
    if not email:
        return False, 403, "Sign-in carries no email."
    if not config.may_read(email):
        log.warning("Denied read for %s", email)
        return False, 403, "This account does not have access."
    if request.method in _WRITE_METHODS and not config.may_write(email):
        log.warning("Denied write for %s on %s", email, request.url.path)
        return False, 403, "This account has read-only access."

    request.state.user_email = email
    return True, 200, ""


def _verify_tailscale(request) -> tuple[bool, int, str]:
    """Trust the identity `tailscale serve` stamped on this request.

    No signature to check: see the module docstring for why that is acceptable here and what
    it does and does not buy. Fails closed when the header is absent, which is what makes a
    directly-reachable port refuse anonymous callers rather than serve them the dashboard.
    """
    email = (request.headers.get(_TAILSCALE_IDENTITY_HEADER) or "").strip().lower()
    if not email:
        return False, 401, "Not signed in."
    return _authorize(email, request)


async def verify(request) -> tuple[bool, int, str]:
    """Return (ok, status, message) for a request. Never raises."""
    if request.url.path in _EXEMPT_PATHS:
        return True, 200, ""

    mode = config.mode
    if mode == "tailscale":
        return _verify_tailscale(request)
    if mode != "cloudflare":
        return True, 200, ""

    token = _token_from(request)
    if not token:
        return False, 401, "Not signed in."

    try:
        key = await _signing_key(token)
        if key is None:
            log.warning("No Cloudflare signing key matches this token")
            return False, 401, "Sign-in is not valid."
        claims = jwt.decode(
            token,
            key.key,
            algorithms=_ALGORITHMS,
            audience=config.aud,
            issuer=config.issuer,
        )
    except httpx.HTTPError:
        # Cloudflare's key endpoint is unreachable. Fail closed: this process is only ever
        # reachable through Cloudflare, so "cannot verify" and "must not serve" coincide.
        log.exception("Could not reach Cloudflare Access certs")
        return False, 503, "Cannot verify sign-in right now."
    except jwt.PyJWTError as exc:
        log.warning("Rejected Access token: %s", exc)
        return False, 401, "Sign-in is not valid."

    return _authorize(str(claims.get("email") or "").lower(), request)


def log_startup_state() -> None:
    readers = ", ".join(sorted(config.allowed)) or "anyone the gate admits"
    writers = ", ".join(sorted(config.writers)) or "same as readers"
    mode = config.mode

    if not mode:
        log.warning(
            "No access gate is configured -- every endpoint is open, including the delete "
            "routes. Fine for local development; set CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD, "
            "or LEADLENS_TAILSCALE_AUTH=1, before exposing this process to a network."
        )
        return

    if mode == "tailscale":
        log.info(
            "Tailscale identity enforced (header=%s, readers=%s, writers=%s)",
            _TAILSCALE_IDENTITY_HEADER,
            readers,
            writers,
        )
        # Said out loud on every boot because this mode's security rests on a deployment
        # detail that lives in another file, and a stray `ports:` mapping would quietly
        # convert a header from "identity" into "anything the caller feels like claiming".
        log.warning(
            "Tailscale mode trusts a proxy header. This is only sound while the app is "
            "reachable via `tailscale serve` alone -- keep the loopback binding from "
            "docker-compose.tailscale.yml and publish no host port."
        )
        if config.team_domain or config.aud:
            log.warning(
                "Partial Cloudflare Access config present but ignored in Tailscale mode; "
                "set both CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD to use Cloudflare instead."
            )
        return

    log.info(
        "Cloudflare Access enforced (issuer=%s, readers=%s, writers=%s)",
        config.issuer,
        readers,
        writers,
    )
    if config.tailscale:
        log.info("LEADLENS_TAILSCALE_AUTH is set but Cloudflare Access takes precedence.")
