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

**HTTP Basic Auth** (`BASIC_AUTH_USER` / `BASIC_AUTH_PASS`). For hosts with no tunnel and no
tailnet in front -- a plain public PaaS deploy (Render, etc.) -- there is no proxy to establish
identity, so this is a single shared credential checked here instead. It is strictly weaker
than the other two modes: one password for everyone, no reader/writer split, and a network
attacker who guesses it is in. It exists only to keep an unlisted demo deploy from being wide
open to anyone with the URL; it is not a substitute for Cloudflare Access or Tailscale on a
deployment that holds real customer data.

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
    BASIC_AUTH_USER           shared username for HTTP Basic Auth (lowest-priority mode)
    BASIC_AUTH_PASS           shared password for HTTP Basic Auth
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import jwt
from jwt import PyJWKSet

log = logging.getLogger("leadlens.auth")

# Cloudflare signs Access tokens with RS256. Pinning the algorithm list is what stops an
# "alg" confusion attack, where a forged token claims HS256 and tricks the verifier into
# validating it with the public key as a shared secret.
_ALGORITHMS = ["RS256"]

# Uptime checks and container healthchecks must not require a login.
_EXEMPT_PATHS = frozenset({"/api/health", "/api/auth/status"})

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_JWKS_TTL_SECONDS = 600
_PASSWORD_ITERATIONS = 260_000
_USER_ROLES = frozenset({"admin", "manager", "staff"})
_ACTIVE_STATUS = "active"

# Set by `tailscale serve` on every proxied request, and stripped from whatever the client
# sent, so it cannot be smuggled in from the far side of the proxy. Note that `tailscale
# funnel` is a different feature: funnel traffic comes from the public internet and carries
# no identity, so it arrives here with no header and is refused. That is the correct outcome
# -- funnel would put this database on the open internet.
_TAILSCALE_IDENTITY_HEADER = "Tailscale-User-Login"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _db_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.getenv("LEADLENS_DATA_DIR", root / "data"))
    return Path(os.getenv("LEADLENS_DB_PATH", data_dir / "leadlens.db"))


def _connect_users():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _user_tables_ready(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_users'"
    ).fetchone()
    return row is not None


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt),
            int(iterations),
        )
        return secrets.compare_digest(base64.b64encode(digest).decode("ascii"), expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def _normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def _clean_role(role: str | None) -> str:
    value = str(role or "staff").strip().lower()
    if value not in _USER_ROLES:
        raise ValueError("Role must be admin, manager, or staff.")
    return value


def _clean_status(status: str | None) -> str:
    value = str(status or _ACTIVE_STATUS).strip().lower()
    if value not in {"active", "disabled"}:
        raise ValueError("Status must be active or disabled.")
    return value


def _role_may_write(role: str) -> bool:
    return role in {"admin", "manager"}


def _record_user_audit(db, actor: str, action: str, target: str = "", detail: str = "") -> None:
    if not _user_tables_ready(db):
        return
    db.execute(
        "INSERT INTO app_user_audit(actor_email, action, target_email, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (_normalize_email(actor), action, _normalize_email(target), detail, _utc_now()),
    )


def _user_public(row) -> dict:
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "full_name": row["full_name"] or "",
        "role": row["role"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
        "has_password": bool(row["password_hash"]),
    }


def _user_by_email(db, email: str):
    if not _user_tables_ready(db):
        return None
    return db.execute("SELECT * FROM app_users WHERE email=?", (_normalize_email(email),)).fetchone()


def ensure_basic_user(db=None) -> None:
    """Keep the environment Basic Auth account available as an admin user."""
    username = (os.getenv("BASIC_AUTH_USER") or "").strip()
    password = os.getenv("BASIC_AUTH_PASS") or ""
    if not username or not password:
        return
    owns = db is None
    conn = db or _connect_users()
    try:
        if not _user_tables_ready(conn):
            return
        email = _normalize_email(username)
        now = _utc_now()
        existing = _user_by_email(conn, email)
        hashed = _hash_password(password)
        if existing:
            if (
                existing["role"] != "admin"
                or existing["status"] != "active"
                or not _verify_password(password, existing["password_hash"])
            ):
                conn.execute(
                    """UPDATE app_users
                       SET role='admin', status='active', password_hash=?, updated_at=?
                       WHERE email=?""",
                    (hashed, now, email),
                )
        else:
            conn.execute(
                """INSERT INTO app_users(email, full_name, role, status, password_hash, created_at, updated_at)
                   VALUES (?, ?, 'admin', 'active', ?, ?, ?)""",
                (email, "Administrator", hashed, now, now),
            )
        if owns:
            conn.commit()
    finally:
        if owns:
            conn.close()


def list_users() -> list[dict]:
    with _connect_users() as db:
        if not _user_tables_ready(db):
            return []
        rows = db.execute("SELECT * FROM app_users ORDER BY status, role, email").fetchall()
        return [_user_public(row) for row in rows]


def list_user_audit(limit: int = 80) -> list[dict]:
    with _connect_users() as db:
        if not _user_tables_ready(db):
            return []
        rows = db.execute(
            """SELECT id, actor_email, action, target_email, detail, created_at
               FROM app_user_audit ORDER BY datetime(created_at) DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def _active_admin_count(db, exclude_id: int | None = None) -> int:
    params: list[object] = []
    where = "role='admin' AND status='active'"
    if exclude_id is not None:
        where += " AND id<>?"
        params.append(exclude_id)
    return int(db.execute(f"SELECT COUNT(*) FROM app_users WHERE {where}", params).fetchone()[0])


def save_user(payload: dict, actor: str, user_id: int | None = None) -> dict:
    email = _normalize_email(payload.get("email", ""))
    if not email or "@" not in email:
        raise ValueError("A valid email is required.")
    role = _clean_role(payload.get("role"))
    status = _clean_status(payload.get("status"))
    full_name = str(payload.get("full_name") or "").strip()
    password = str(payload.get("password") or "")
    now = _utc_now()
    with _connect_users() as db:
        if not _user_tables_ready(db):
            raise ValueError("User table is not ready.")
        if user_id is None:
            if not password:
                raise ValueError("A password is required for new users.")
            db.execute(
                """INSERT INTO app_users(email, full_name, role, status, password_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (email, full_name, role, status, _hash_password(password), now, now),
            )
            _record_user_audit(db, actor, "created_user", email, f"role={role}; status={status}")
            row = _user_by_email(db, email)
            return _user_public(row)

        existing = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
        if not existing:
            raise ValueError("User not found.")
        if existing["role"] == "admin" and existing["status"] == "active" and (
            role != "admin" or status != "active"
        ) and _active_admin_count(db, exclude_id=user_id) == 0:
            raise ValueError("At least one active admin is required.")
        fields = ["email=?", "full_name=?", "role=?", "status=?", "updated_at=?"]
        values: list[object] = [email, full_name, role, status, now]
        detail = f"role={role}; status={status}"
        if password:
            fields.append("password_hash=?")
            values.append(_hash_password(password))
            detail += "; password=changed"
        values.append(user_id)
        db.execute(f"UPDATE app_users SET {', '.join(fields)} WHERE id=?", values)
        _record_user_audit(db, actor, "updated_user", email, detail)
        row = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
        return _user_public(row)


def delete_user(user_id: int, actor: str) -> dict:
    with _connect_users() as db:
        if not _user_tables_ready(db):
            raise ValueError("User table is not ready.")
        row = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found.")
        if row["role"] == "admin" and row["status"] == "active" and _active_admin_count(db, exclude_id=user_id) == 0:
            raise ValueError("At least one active admin is required.")
        email = row["email"]
        db.execute("DELETE FROM app_users WHERE id=?", (user_id,))
        _record_user_audit(db, actor, "deleted_user", email)
        return {"deleted": True, "email": email}


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
        self.basic_user = (os.getenv("BASIC_AUTH_USER") or "").strip()
        self.basic_pass = os.getenv("BASIC_AUTH_PASS") or ""

    @property
    def enabled(self) -> bool:
        """True when some identity gate is in force. Kept as the single 'is this locked down'
        question so callers do not have to know which topology is deployed."""
        return bool(self.mode)

    @property
    def mode(self) -> str:
        """"cloudflare", "tailscale", "basic", or "" for inert.

        Cloudflare wins when both are configured: a verified signature is strictly stronger
        evidence than a proxy header, so if the JWT is available there is no reason to fall
        back to trusting a hop. Basic Auth is last resort, only when neither real topology is
        configured -- see the module docstring for why it is weaker than both.
        """
        if self.team_domain and self.aud:
            return "cloudflare"
        if self.tailscale:
            return "tailscale"
        if self.basic_user and self.basic_pass:
            return "basic"
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
    try:
        with _connect_users() as db:
            user = _user_by_email(db, email)
            if user and user["status"] == _ACTIVE_STATUS:
                request.state.user_role = user["role"]
                request.state.user_name = user["full_name"] or ""
            else:
                request.state.user_role = "manager" if config.may_write(email) else "staff"
                request.state.user_name = ""
    except sqlite3.Error:
        request.state.user_role = "manager" if config.may_write(email) else "staff"
        request.state.user_name = ""
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


# RFC 7617 challenge, sent whenever Basic Auth is the active mode and the request is not yet
# authorized -- this is what makes a browser pop its native login dialog instead of just
# showing a bare 401 page.
_BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="LeadLens", charset="UTF-8"'}


def is_exempt_request(request) -> bool:
    """Allow health checks and the app shell to load before Basic Auth is supplied.

    Cloudflare and Tailscale deployments still gate the shell before it reaches FastAPI. Basic
    Auth is different: the browser's built-in challenge appears before React can render, so
    the shell is public while every `/api/*` data route stays protected.
    """
    path = request.url.path
    if path in _EXEMPT_PATHS:
        return True
    return config.mode == "basic" and request.method in {"GET", "HEAD"} and not path.startswith("/api/")


def _basic_challenge_for(request) -> dict[str, str]:
    # The custom React login probes /api/auth/me and should render its own error. Other
    # protected routes still advertise RFC 7617 so non-React clients understand the gate.
    return {} if request.url.path == "/api/auth/me" else _BASIC_CHALLENGE


def _verify_basic(request) -> tuple[bool, int, str, dict[str, str]]:
    """Single shared credential -- see the module docstring for why this is a last resort.

    Uses constant-time comparison so response timing cannot leak how much of the guess was
    right, same reasoning as any password check.
    """
    header = request.headers.get("Authorization", "")
    challenge = _basic_challenge_for(request)
    if not header.startswith("Basic "):
        return False, 401, "Not signed in.", challenge
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False, 401, "Sign-in is not valid.", challenge
    username, _, password = decoded.partition(":")
    normalized = _normalize_email(username)
    try:
        ensure_basic_user()
        with _connect_users() as db:
            user = _user_by_email(db, normalized)
            if user:
                if user["status"] != _ACTIVE_STATUS or not _verify_password(password, user["password_hash"]):
                    log.warning("Rejected app user sign-in attempt for %r", username)
                    return False, 401, "Sign-in is not valid.", challenge
                if request.method in _WRITE_METHODS and not _role_may_write(user["role"]):
                    log.warning("Denied write for %s on %s", normalized, request.url.path)
                    return False, 403, "This account has read-only access.", {}
                if request.url.path == "/api/auth/me":
                    now = _utc_now()
                    db.execute("UPDATE app_users SET last_login_at=?, updated_at=? WHERE id=?", (now, now, user["id"]))
                    _record_user_audit(db, normalized, "signed_in", normalized)
                request.state.user_email = normalized
                request.state.user_role = user["role"]
                request.state.user_name = user["full_name"] or ""
                return True, 200, "", {}
    except sqlite3.Error:
        pass
    if not (config.basic_user and config.basic_pass):
        log.warning("Rejected Basic Auth attempt for user %r", username)
        return False, 401, "Sign-in is not valid.", challenge
    user_ok = secrets.compare_digest(username, config.basic_user)
    pass_ok = secrets.compare_digest(password, config.basic_pass)
    if not (user_ok and pass_ok):
        log.warning("Rejected Basic Auth attempt for user %r", username)
        return False, 401, "Sign-in is not valid.", challenge
    request.state.user_email = normalized or username
    request.state.user_role = "admin"
    request.state.user_name = "Administrator"
    return True, 200, "", {}


async def verify(request) -> tuple[bool, int, str, dict[str, str]]:
    """Return (ok, status, message, extra_headers) for a request. Never raises."""
    if request.url.path in _EXEMPT_PATHS:
        return True, 200, "", {}

    mode = config.mode
    if mode == "tailscale":
        ok, status, message = _verify_tailscale(request)
        return ok, status, message, {}
    if mode == "basic":
        return _verify_basic(request)
    if mode != "cloudflare":
        return True, 200, "", {}

    token = _token_from(request)
    if not token:
        return False, 401, "Not signed in.", {}

    try:
        key = await _signing_key(token)
        if key is None:
            log.warning("No Cloudflare signing key matches this token")
            return False, 401, "Sign-in is not valid.", {}
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
        return False, 503, "Cannot verify sign-in right now.", {}
    except jwt.PyJWTError as exc:
        log.warning("Rejected Access token: %s", exc)
        return False, 401, "Sign-in is not valid.", {}

    ok, status, message = _authorize(str(claims.get("email") or "").lower(), request)
    return ok, status, message, {}


def require_gate_or_die() -> None:
    """Refuse to boot into an open state on a deployment that is meant to be gated.

    An inert gate is invisible from the outside -- the dashboard looks identical whether or not
    every DELETE route is open to anyone with the URL (this actually happened on the first Render
    deploy, 2026-08-14). A warning log did not stop it, so on a host that declares itself public
    this is promoted to a hard failure: the container will not start until a gate is configured.

    Triggers when `LEADLENS_REQUIRE_AUTH` is truthy, or when any known public-PaaS marker is
    present. Local dev and the test suite set none of them, so they stay inert.

    The marker list has to be maintained per platform, which is its weakness: a gate keyed on
    `RENDER` alone is silently inert the moment the app is deployed somewhere else, which is
    exactly the incident this function exists to prevent, reintroduced by changing host.
    `RAILWAY_ENVIRONMENT` was added 2026-08-16 when the demo moved to Railway. Anything
    internet-facing that is not in this list still needs `LEADLENS_REQUIRE_AUTH=1` set by hand.
    """
    markers = ("RENDER", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME", "DYNO", "WEBSITE_INSTANCE_ID")
    detected = next((name for name in markers if (os.getenv(name) or "").strip()), None)
    require = _flag("LEADLENS_REQUIRE_AUTH") or detected is not None
    if require and detected == "RENDER" and not config.mode:
        config.basic_user = "admin"
        config.basic_pass = secrets.token_urlsafe(24)
        log.warning(
            "Render started without BASIC_AUTH_USER/BASIC_AUTH_PASS. Generated temporary "
            "Basic Auth credentials for this boot: username=%s password=%s. Set permanent "
            "values in Render's Environment tab; this password changes on every restart.",
            config.basic_user,
            config.basic_pass,
        )
        return
    if require and not config.mode:
        raise RuntimeError(
            "Refusing to start with no access gate on a deployment that requires one "
            f"(detected: {detected or 'LEADLENS_REQUIRE_AUTH'}). "
            "Set BASIC_AUTH_USER + BASIC_AUTH_PASS (public PaaS), or CF_ACCESS_TEAM_DOMAIN + "
            "CF_ACCESS_AUD, or LEADLENS_TAILSCALE_AUTH=1 -- see backend/auth.py. To intentionally "
            f"run open (local only), unset LEADLENS_REQUIRE_AUTH and {detected or 'the PaaS marker'}."
        )


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

    if mode == "basic":
        log.warning(
            "HTTP Basic Auth enforced for user %r. This is a single shared credential with "
            "no reader/writer split -- fine for an unlisted demo deploy, not a substitute for "
            "Cloudflare Access or Tailscale on a deployment holding real customer data.",
            config.basic_user,
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
