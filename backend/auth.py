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
import threading
import time
from datetime import datetime, timedelta, timezone
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

# POSTs that are part of establishing or ending a session rather than mutating data. They must
# bypass the writer-role check: /api/auth/login is how a *read-only* account signs in, and
# 403-ing it would lock every staff user out of the dashboard entirely.
_SESSION_PATHS = frozenset({"/api/auth/login", "/api/auth/logout"})


def _is_write(request) -> bool:
    return request.method in _WRITE_METHODS and request.url.path not in _SESSION_PATHS

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


# ---- Verified-credential cache -----------------------------------------------------------
# HTTP Basic Auth resends the password on every single request, and verifying it costs one
# PBKDF2 derivation at _PASSWORD_ITERATIONS -- deliberately expensive, which is right for a
# login form and wrong for a hot path. Unmitigated, a signed-in client polling the dashboard
# can pin the one worker's CPU purely on key derivation, and any request-per-second the app
# serves is a request-per-second of hashing.
#
# So a *successful* verification is remembered for a short TTL, keyed by a salted digest of the
# credential rather than the credential itself, so the process is not holding recoverable
# passwords in memory. Failures are never cached: they stay full-price, which is what keeps
# guessing expensive, and they are separately bounded by the per-IP lockout in
# backend/security.py. The TTL is short and any write to app_users clears the cache outright
# (see _invalidate_user_caches), so disabling an account takes effect promptly.
_CREDENTIAL_CACHE_TTL = 60.0
_CREDENTIAL_CACHE_MAX = 256
_credential_cache_salt = secrets.token_bytes(32)


class _VerifiedCredentials:
    """TTL cache of credential digest -> (email, role, full_name). Thread-safe."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, tuple[str, str, str]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str, password: str) -> str:
        # Length-prefixed so that a username/password pair cannot be re-split to collide with
        # a different pair (e.g. "ab"/"c" vs "a"/"bc").
        payload = f"{len(username)}:{username}:{len(password)}:{password}".encode("utf-8")
        return hashlib.blake2b(payload, key=_credential_cache_salt, digest_size=32).hexdigest()

    def get(self, username: str, password: str) -> tuple[str, str, str] | None:
        key = self._key(username, password)
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def put(self, username: str, password: str, value: tuple[str, str, str]) -> None:
        key = self._key(username, password)
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= _CREDENTIAL_CACHE_MAX:
                for stale in [k for k, (expires_at, _) in self._entries.items() if expires_at <= now]:
                    self._entries.pop(stale, None)
                if len(self._entries) >= _CREDENTIAL_CACHE_MAX:
                    self._entries.clear()
            self._entries[key] = (now + _CREDENTIAL_CACHE_TTL, value)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_verified_credentials = _VerifiedCredentials()


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


def _staff_may_write_request(request) -> bool:
    path = request.url.path
    return (
        request.method == "PATCH" and path.startswith("/api/leads/") and path.count("/") == 3
    ) or (
        request.method == "POST" and path == "/api/leads/bulk-quality"
    )


def _role_may_write_request(role: str, request) -> bool:
    return _role_may_write(role) or (role == "staff" and _staff_may_write_request(request))


# ---- Sign-in sessions --------------------------------------------------------------------
# What the browser holds after signing in. Previously it held the `Basic base64(user:pass)`
# header itself, parked in localStorage for 30 days: that string decodes straight back to the
# plaintext password, so it was a stored credential rather than a session, and nothing short of
# a password change could revoke it. A session token is opaque, server-issued, individually
# revocable, and expires on a date the server controls.
#
# Only the SHA-256 of the token is stored. The token is high-entropy and random (not a
# password), so a single unsalted hash is the right construction here -- there is nothing to
# brute-force -- and it means a leaked copy of the database yields no usable sessions.
SESSION_TTL_DAYS = int(os.getenv("LEADLENS_SESSION_TTL_DAYS", "30") or 30)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sessions_ready(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_sessions'"
    ).fetchone()
    return row is not None


def _purge_expired_sessions(conn) -> None:
    conn.execute("DELETE FROM app_sessions WHERE expires_at <= ?", (_utc_now(),))


def create_session(email: str) -> dict:
    """Mint a session for an already-authenticated email. Returns the raw token once."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    with _connect_users() as db:
        if not _sessions_ready(db):
            raise ValueError("Session table is not ready.")
        _purge_expired_sessions(db)
        db.execute(
            "INSERT INTO app_sessions(token_hash, email, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash_session_token(token), _normalize_email(email),
             now.isoformat(timespec="microseconds"), expires.isoformat(timespec="microseconds"),
             now.isoformat(timespec="microseconds")),
        )
    return {"token": token, "expires_at": expires.isoformat(timespec="microseconds")}


def revoke_session(token: str) -> None:
    if not token:
        return
    try:
        with _connect_users() as db:
            if _sessions_ready(db):
                db.execute("DELETE FROM app_sessions WHERE token_hash=?", (_hash_session_token(token),))
    except sqlite3.Error:
        pass


def revoke_sessions_for(email: str) -> None:
    """Drop every session belonging to one account -- used when it is disabled or deleted."""
    try:
        with _connect_users() as db:
            if _sessions_ready(db):
                db.execute("DELETE FROM app_sessions WHERE email=?", (_normalize_email(email),))
    except sqlite3.Error:
        pass


def resolve_session(token: str) -> tuple[str, str, str] | None:
    """(email, role, full_name) for a live session, or None.

    The role is re-read from app_users on every call rather than being baked into the session
    at sign-in. That is what makes a demotion or a disable take effect immediately instead of
    at the session's natural expiry -- the session proves *who*, never *what they may do*.
    """
    if not token:
        return None
    try:
        with _connect_users() as db:
            if not _sessions_ready(db):
                return None
            row = db.execute(
                "SELECT email, expires_at FROM app_sessions WHERE token_hash=?",
                (_hash_session_token(token),),
            ).fetchone()
            if row is None:
                return None
            if str(row["expires_at"]) <= _utc_now():
                db.execute("DELETE FROM app_sessions WHERE token_hash=?", (_hash_session_token(token),))
                return None
            email = _normalize_email(row["email"])
            db.execute(
                "UPDATE app_sessions SET last_seen_at=? WHERE token_hash=?",
                (_utc_now(), _hash_session_token(token)),
            )
            user = _user_by_email(db, email)
            if user is not None:
                if user["status"] != _ACTIVE_STATUS:
                    db.execute("DELETE FROM app_sessions WHERE email=?", (email,))
                    return None
                return email, user["role"], user["full_name"] or ""
            # No app_users row: the environment-only BASIC_AUTH account, which is admin by
            # definition. Anything else with a session but no row has had its account deleted.
            if config.basic_user and _normalize_email(config.basic_user) == email:
                return email, "admin", "Administrator"
            db.execute("DELETE FROM app_sessions WHERE email=?", (email,))
            return None
    except sqlite3.Error:
        return None


def _bearer_token(request) -> str:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return ""


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


# Once the env Basic Auth account has been reconciled into app_users, doing it again on the
# next request is pure waste. This is not a security decision but a cost one: reconciling runs
# PBKDF2 at _PASSWORD_ITERATIONS, and this function used to be called on *every* authenticated
# request, so each one burned two full key derivations (one to verify, one to build a
# replacement hash that was usually discarded). That is roughly a quarter-second of CPU per
# request on the single worker, and an unauthenticated caller could drive it just by sending an
# Authorization header -- a work-amplification lever pointed straight at the process. The flag
# is cleared whenever the credential changes, so a rotated BASIC_AUTH_PASS still takes effect.
_basic_user_synced_for: tuple[str, str] | None = None


def ensure_basic_user(db=None) -> None:
    """Keep the environment Basic Auth account available as an admin user.

    Idempotent and self-caching: after the first successful reconcile for a given
    BASIC_AUTH_USER/PASS pair this returns immediately, so it is safe to call on a hot path.
    """
    global _basic_user_synced_for
    username = (os.getenv("BASIC_AUTH_USER") or "").strip()
    password = os.getenv("BASIC_AUTH_PASS") or ""
    if not username or not password:
        return
    if _basic_user_synced_for == (username, password):
        return
    owns = db is None
    conn = db or _connect_users()
    try:
        if not _user_tables_ready(conn):
            return
        email = _normalize_email(username)
        now = _utc_now()
        existing = _user_by_email(conn, email)
        if existing:
            # Hash only when a write is actually needed. _verify_password is one derivation;
            # the old code paid for a second one building `hashed` before knowing whether the
            # row needed updating at all.
            if (
                existing["role"] != "admin"
                or existing["status"] != "active"
                or not _verify_password(password, existing["password_hash"])
            ):
                conn.execute(
                    """UPDATE app_users
                       SET role='admin', status='active', password_hash=?, updated_at=?
                       WHERE email=?""",
                    (_hash_password(password), now, email),
                )
        else:
            conn.execute(
                """INSERT INTO app_users(email, full_name, role, status, password_hash, created_at, updated_at)
                   VALUES (?, ?, 'admin', 'active', ?, ?, ?)""",
                (email, "Administrator", _hash_password(password), now, now),
            )
        if owns:
            conn.commit()
        _basic_user_synced_for = (username, password)
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


def _invalidate_user_caches() -> None:
    """Drop both credential caches after any write to app_users.

    Without this, the admin screen's edits would not be visible to the auth path until the
    process restarted: a disabled account would keep authenticating for the cache TTL, and a
    changed password would keep being reverted to the environment value by a stale reconcile
    flag. Correctness of a *revocation* is the reason this is unconditional rather than
    targeted at the one row that changed.
    """
    global _basic_user_synced_for
    _basic_user_synced_for = None
    _verified_credentials.clear()


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
            _invalidate_user_caches()
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
        # A disabled account, a changed password, or a changed email must not leave a live
        # session behind -- otherwise "disable this user" is advisory until their token expires.
        if status != _ACTIVE_STATUS or password or email != _normalize_email(existing["email"]):
            for affected in {email, _normalize_email(existing["email"])}:
                if _sessions_ready(db):
                    db.execute("DELETE FROM app_sessions WHERE email=?", (affected,))
        _record_user_audit(db, actor, "updated_user", email, detail)
        row = db.execute("SELECT * FROM app_users WHERE id=?", (user_id,)).fetchone()
        _invalidate_user_caches()
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
        if _sessions_ready(db):
            db.execute("DELETE FROM app_sessions WHERE email=?", (_normalize_email(email),))
        _record_user_audit(db, actor, "deleted_user", email)
        _invalidate_user_caches()
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
    if _is_write(request) and not _role_may_write_request(request.state.user_role, request):
        log.warning("Denied write for %s on %s", email, request.url.path)
        return False, 403, "This account has read-only access."
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

    Two credential shapes reach here. `Bearer <token>` is a session minted by
    /api/auth/login and is what the dashboard sends after sign-in; `Basic <base64>` is the raw
    credential, still accepted so that curl, the container healthcheck and any non-browser
    client keep working, and so that /api/auth/login itself has something to authenticate.
    """
    challenge = _basic_challenge_for(request)

    token = _bearer_token(request)
    if token:
        resolved = resolve_session(token)
        if resolved is None:
            return False, 401, "Session has expired. Sign in again.", challenge
        email, role, name = resolved
        if _is_write(request) and not _role_may_write_request(role, request):
            log.warning("Denied write for %s on %s", email, request.url.path)
            return False, 403, "This account has read-only access.", {}
        request.state.user_email = email
        request.state.user_role = role
        request.state.user_name = name
        return True, 200, "", {}

    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False, 401, "Not signed in.", challenge
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False, 401, "Sign-in is not valid.", challenge
    username, _, password = decoded.partition(":")
    normalized = _normalize_email(username)

    # A credential verified moments ago skips both the PBKDF2 derivation and the database
    # round-trip. The write-side check below still runs against the cached role, so a
    # read-only account cannot slip a write through on a cache hit.
    cached = _verified_credentials.get(username, password)
    if cached is not None:
        cached_email, cached_role, cached_name = cached
        if _is_write(request) and not _role_may_write_request(cached_role, request):
            log.warning("Denied write for %s on %s", cached_email, request.url.path)
            return False, 403, "This account has read-only access.", {}
        # The sign-in paths are deliberately excluded from the cache so that last_login_at
        # and the audit row still record every real sign-in.
        if request.url.path not in ("/api/auth/me", "/api/auth/login"):
            request.state.user_email = cached_email
            request.state.user_role = cached_role
            request.state.user_name = cached_name
            return True, 200, "", {}

    try:
        ensure_basic_user()
        with _connect_users() as db:
            user = _user_by_email(db, normalized)
            if user:
                if user["status"] != _ACTIVE_STATUS or not _verify_password(password, user["password_hash"]):
                    log.warning("Rejected app user sign-in attempt for %r", username)
                    return False, 401, "Sign-in is not valid.", challenge
                if _is_write(request) and not _role_may_write_request(user["role"], request):
                    log.warning("Denied write for %s on %s", normalized, request.url.path)
                    return False, 403, "This account has read-only access.", {}
                if request.url.path in ("/api/auth/me", "/api/auth/login"):
                    now = _utc_now()
                    db.execute("UPDATE app_users SET last_login_at=?, updated_at=? WHERE id=?", (now, now, user["id"]))
                    _record_user_audit(db, normalized, "signed_in", normalized)
                request.state.user_email = normalized
                request.state.user_role = user["role"]
                request.state.user_name = user["full_name"] or ""
                _verified_credentials.put(
                    username, password, (normalized, user["role"], user["full_name"] or "")
                )
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
    _verified_credentials.put(username, password, (normalized or username, "admin", "Administrator"))
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
    # Render used to be special-cased here: booting with no gate would mint a random password
    # and print it to the log. That was a fail-*open* dressed as a fail-closed, and it defeated
    # this function twice over. It wrote a live credential into Render's log stream, which is
    # retained, searchable, and visible to everyone on the account -- the one place a password
    # must never be -- and it meant the single deployment style this check was written in
    # response to (the open Render deploy of 2026-08-14) was the only one it did not stop.
    # render.yaml sets BASIC_AUTH_PASS with `generateValue: true`, so the blueprint path
    # already has a real gate; a Render deploy that does not is a misconfiguration and now
    # fails like every other host.
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
