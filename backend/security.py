"""Request-level hardening that sits in front of the routes: body-size caps, a small
in-process rate limiter, brute-force throttling for the shared Basic Auth credential, and the
security response headers (CSP included).

None of this replaces `backend/auth.py` -- that decides *who* may call. This module limits
*how hard* anyone (authenticated or not) can lean on the process, and hardens the browser side
of the served dashboard. It is deliberately dependency-free and single-process: the app pins
`--workers 1` (one SQLite writer), so an in-memory limiter keyed by client IP is coherent
without Redis. Behind Render/Cloudflare/Tailscale the real client IP arrives in
`X-Forwarded-For`; that header is only trustworthy because one of those proxies sets it, which
is the same topology assumption `auth.py` already documents.

Everything here is environment-tunable but ships with sane defaults, and is inert for nothing --
it applies in local dev too, where the limits are generous enough never to be hit by a human.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


# ---- Body size ---------------------------------------------------------------------------
# A hard ceiling on any request body, checked from Content-Length before the body is read into
# memory. The upload endpoints load the whole file with `await file.read()`, so without this a
# single large POST would OOM the one worker (Render free is ~512 MB). 30 MB comfortably clears
# the largest real Meta export while making a memory-exhaustion DoS impractical.
MAX_BODY_BYTES = _int_env("LEADLENS_MAX_BODY_MB", 30) * 1024 * 1024

# A tighter cap for the file-upload path specifically, enforced while reading so the process
# never buffers more than this regardless of a lying Content-Length.
MAX_UPLOAD_BYTES = _int_env("LEADLENS_MAX_UPLOAD_MB", 25) * 1024 * 1024

# When set, the data-import endpoints refuse to run. This is for the public Render demo, whose
# whole premise is "no real customer data" -- a policy that was previously enforced by nothing.
DEMO_MODE = _flag("LEADLENS_DEMO_MODE")

# Preview tokens are uuid4().hex -- 32 lowercase hex chars. Validating the shape before it
# reaches `PREVIEW_DIR.glob(f"{token}.*")` stops a caller smuggling glob metacharacters
# (`*`, `?`, `[`) in to match previews other than their own.
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


# ---- Rate limiting -----------------------------------------------------------------------
GENERAL_LIMIT = _int_env("LEADLENS_RATE_GENERAL", 600)       # requests
GENERAL_WINDOW = _int_env("LEADLENS_RATE_GENERAL_WINDOW", 60)  # seconds

RETRAIN_LIMIT = _int_env("LEADLENS_RATE_RETRAIN", 6)
RETRAIN_WINDOW = _int_env("LEADLENS_RATE_RETRAIN_WINDOW", 300)

# Brute-force throttle for the single shared Basic Auth password. After this many failed
# sign-ins from one IP inside the window, that IP's requests are refused with 429 until the
# window rolls off. Kept per-IP (not global) so an attacker cannot lock the real user out.
AUTH_FAIL_LIMIT = _int_env("LEADLENS_AUTH_FAIL_LIMIT", 15)
AUTH_FAIL_WINDOW = _int_env("LEADLENS_AUTH_FAIL_WINDOW", 900)


# Hard ceiling on how many distinct keys (client IPs) a limiter will track at once. Without
# it the limiter is itself a memory-exhaustion vector: every unseen IP allocates a dict entry
# and a deque, and an attacker rotating source addresses -- or forged `X-Forwarded-For` hops,
# before `client_ip` was tightened below -- adds one per request and never frees them. At the
# cap the oldest-touched keys are evicted, which at worst resets the counter for a client that
# has been idle longer than everyone else in the table.
MAX_TRACKED_KEYS = _int_env("LEADLENS_RATE_MAX_KEYS", 20_000)


class _SlidingWindow:
    """A per-key sliding-window counter. Thread-safe: the background retrain thread and the
    request path can both touch it. Old timestamps are evicted lazily on each check, empty
    keys are dropped, and the key table is capped at MAX_TRACKED_KEYS, so memory tracks active
    clients rather than growing without bound."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _sweep_locked(self, window: int) -> None:
        """Drop keys whose newest event has already aged out. Called only when the table is at
        the cap, so the O(n) pass is amortised across at least MAX_TRACKED_KEYS requests."""
        cutoff = time.monotonic() - window
        for key in [k for k, bucket in self._events.items() if not bucket or bucket[-1] < cutoff]:
            self._events.pop(key, None)
        # Everything still in the table is genuinely active. Evict oldest-touched first so the
        # cap holds even under a flood of live keys.
        while len(self._events) >= MAX_TRACKED_KEYS:
            oldest = min(self._events, key=lambda k: self._events[k][-1] if self._events[k] else 0.0)
            self._events.pop(oldest, None)

    def hit(self, key: str, limit: int, window: int) -> bool:
        """Record one event for `key`; return True if it is within `limit` over `window`."""
        if limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            if key not in self._events and len(self._events) >= MAX_TRACKED_KEYS:
                self._sweep_locked(window)
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now)
            # Cap the per-key history too: without this, a caller already over the limit keeps
            # appending timestamps for the whole window, so the memory cost of being blocked
            # grows with the flood instead of flattening out.
            while len(bucket) > limit + 1:
                bucket.popleft()
            return len(bucket) <= limit

    def count(self, key: str, window: int) -> int:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            bucket = self._events.get(key)
            if not bucket:
                return 0
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                self._events.pop(key, None)
                return 0
            return len(bucket)

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


_general = _SlidingWindow()
_retrain = _SlidingWindow()
_auth_fail = _SlidingWindow()


# How many proxies sit in front of this process and append to X-Forwarded-For. Every supported
# topology (Cloudflare tunnel, Tailscale Serve, Render, Railway) is exactly one hop, so that is
# the default; raise it only if you knowingly add another trusted proxy in front.
TRUSTED_PROXY_HOPS = _int_env("LEADLENS_TRUSTED_PROXY_HOPS", 1)


def client_ip(request) -> str:
    """Best-effort caller IP, read from the right-hand end of X-Forwarded-For.

    The left-most entry is the one thing in that header nobody trustworthy wrote: a proxy
    *appends* the peer it saw, so anything already there was supplied by the client. Keying the
    rate limiter and the brute-force throttle on it therefore handed an attacker a free bypass
    -- send a different forged first hop on each request and every counter starts from zero,
    which defeats the Basic Auth lockout entirely and grows the limiter's key table without
    bound. Counting TRUSTED_PROXY_HOPS back from the right instead lands on the address our own
    proxy observed, which is the last entry a client cannot forge.

    With no header at all (local dev, direct connection) this falls back to the socket peer,
    which is unspoofable.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        if hops:
            # Clamp rather than index off the end: a request that arrives with fewer hops than
            # configured (a healthcheck hitting the port directly, say) still resolves to the
            # left-most entry rather than raising.
            index = max(0, len(hops) - max(1, TRUSTED_PROXY_HOPS))
            return hops[index]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def allow_general(ip: str) -> bool:
    return _general.hit(ip, GENERAL_LIMIT, GENERAL_WINDOW)


def allow_retrain(ip: str) -> bool:
    return _retrain.hit(ip, RETRAIN_LIMIT, RETRAIN_WINDOW)


def auth_blocked(ip: str) -> bool:
    """True once this IP has burned through the failed-sign-in budget for the window."""
    return _auth_fail.count(ip, AUTH_FAIL_WINDOW) >= AUTH_FAIL_LIMIT > 0


def record_auth_failure(ip: str) -> None:
    _auth_fail.hit(ip, AUTH_FAIL_LIMIT, AUTH_FAIL_WINDOW)


def clear_auth_failures(ip: str) -> None:
    _auth_fail.clear(ip)


# ---- Security headers --------------------------------------------------------------------
def _inline_script_hashes(html: str) -> list[str]:
    """CSP `'sha256-...'` sources for every inline <script> (no src attr) in the served shell.

    The dashboard's index.html carries one tiny inline script (theme-before-paint), so a strict
    `script-src 'self'` would block it and reintroduce the theme flash. Hashing the exact script
    body lets CSP keep `'unsafe-inline'` off script-src entirely -- the thing a scanner flags --
    while still allowing that one known script. Computed from the file actually served, so it
    stays correct if the script is edited."""
    hashes: list[str] = []
    for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE):
        body = match.group(1)
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    return hashes


def build_csp(index_html: Path | None) -> str:
    script_src = "'self'"
    if index_html and index_html.exists():
        try:
            hashes = _inline_script_hashes(index_html.read_text(encoding="utf-8"))
        except OSError:
            hashes = []
        if hashes:
            script_src = "'self' " + " ".join(hashes)
    # style-src keeps 'unsafe-inline': React and Recharts set element style attributes, and
    # @fontsource injects <style> at runtime -- none of which is an injection sink here, since
    # the app renders no user-supplied HTML. script-src stays hash-pinned (no 'unsafe-inline').
    return "; ".join([
        "default-src 'self'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "style-src 'self' 'unsafe-inline'",
        f"script-src {script_src}",
        "connect-src 'self'",
        "form-action 'self'",
    ])


_CSP = ""


def configure_csp(index_html: Path | None) -> None:
    """Resolve the CSP once at startup from the served shell."""
    global _CSP
    _CSP = build_csp(index_html)


def apply_headers(response, is_https: bool) -> None:
    """Stamp the security headers onto an outgoing response."""
    headers = response.headers
    if _CSP:
        headers.setdefault("Content-Security-Policy", _CSP)
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    # HSTS is meaningful only over TLS; sending it on plain-HTTP localhost dev can wedge the
    # browser onto https for localhost, so it is gated on the request actually being https.
    if is_https:
        headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")


def is_https_request(request) -> bool:
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return getattr(getattr(request, "url", None), "scheme", "") == "https"
