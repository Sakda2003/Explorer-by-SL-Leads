# Access Control & Deploy

Built 2026-07-30, Phase 1 of the "only me and the CEO can reach this" deployment plan.

**Why it exists:** LeadLens had zero authentication while exposing 8 write/delete
endpoints. The database holds ~2,700 lead records with `customer_name`, and `raw_json`
carries the full CRM payload — this is customer PII, not just ad metrics.

## Three topologies (third added 2026-08-14)

`backend/auth.py` supports **three** ways of establishing identity, selected by env vars via
`config.mode` — `"cloudflare"`, `"tailscale"`, `"basic"`, or `""` (inert). Cloudflare and
Tailscale funnel into one `_authorize()` helper with the reader/writer split; Basic Auth is a
single shared credential with no split, checked in its own `_verify_basic()`.

**Basic Auth (`BASIC_AUTH_USER` / `BASIC_AUTH_PASS`) exists for hosts with no tunnel and no
tailnet in front** — a plain public PaaS deploy (Render free tier, etc.) where neither of the
other two topologies is physically possible: there is no proxy in front of the container to
stamp an identity header or terminate a Cloudflare tunnel. It is deliberately the lowest
priority in `config.mode` and is meant only for an unlisted demo deploy carrying no real
customer data — see "Public demo deploy" below. `auth.verify()` now returns a 4-tuple
(`ok, status, message, headers`) instead of 3 — the extra slot carries the `WWW-Authenticate`
challenge header so a browser shows its native login prompt instead of a bare 401 page. All
call sites (`backend/app.py`'s middleware, `tests/test_auth.py`) were updated for the new
shape; `BasicAuthTests` covers the mode.

| | Cloudflare Access | Tailscale Serve |
|---|---|---|
| Runbook | `deploy/RUNBOOK.md` | `deploy/RUNBOOK-TAILSCALE.md` (Linux) / `-WINDOWS.md` |
| Cost | domain ~$12/yr + VPS | $0 (Oracle Always Free) |
| Users | 50 free | **3 free** |
| Identity | RS256 JWT, verified | `Tailscale-User-Login` header |
| Env | `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` | `LEADLENS_TAILSCALE_AUTH=1` |

**Cloudflare wins if both are configured** — a verified signature strictly beats a proxy
header, so there's no reason to fall back to trusting a hop. Tests assert this
(`ModePrecedenceTests`), because the failure mode would be silently *downgrading* a
Cloudflare deployment to header trust by sending one header.

**The Tailscale mode's security is inherited from the topology, not from the header.** It is
only sound while `docker-compose.tailscale.yml`'s `127.0.0.1:8000` binding holds and no host
port is published — a `ports:` mapping converts the header from "identity" into "whatever the
caller claims". That's why: the flag is explicit opt-in (never inferred from the header being
present), startup logs the assumption out loud every boot, and a missing header fails **closed**
(401) so an accidentally-exposed port doesn't serve anonymous scanners. What this mode actually
buys is *role separation among already-authenticated tailnet users* — stopping a third reader
from deleting a lead — not resistance to a network attacker.

`tailscale funnel` must never be used: funnel traffic is public and carries no identity, so
everyone would 401. Fail-closed is correct here; don't work around it.

## Cloudflare specifics

`backend/auth.py` verifies Cloudflare Access JWTs as **defence in depth**, not the
primary gate (Cloudflare Access at the edge is that).

**Env-gated so it's inert by default.** With the Cloudflare env vars unset, `verify()`
passes everything through and logs a warning — local dev and all tests keep working
untouched. `LEADLENS_ALLOWED_EMAILS` = who may read; `LEADLENS_WRITER_EMAILS` = who may
mutate. Listing only the primary user gives the CEO the dashboard without delete/retrain
ability.

**Deliberately does not use `PyJWKClient`** — it makes a blocking call inside async
code and has an internally inconsistent cache. JWKS is fetched with `httpx` (async) and
keys matched by `kid` by hand. Do not "simplify" this back to `PyJWKClient`.

Algorithms pinned to `["RS256"]` (prevents alg-confusion forgery). Fails **closed** if
Cloudflare's certs endpoint is unreachable. `/api/health` is exempt for container
checks.

**Phase 2 (Docker):** two-stage build (`node:24-slim` → `python:3.12-slim`). The image
sets **`LEADLENS_DATA_DIR=/data`, not `LEADLENS_DB_PATH`** — one variable moves
database + uploads + previews onto the volume together; setting only the DB path
strands uploads/previews on the container filesystem. `app` publishes **no host
port** in compose (`expose`, never `ports`) — that's the whole security property; a
`ports:` mapping would silently expose the PII database to the host network.

**Phase 3 (server hardening):** firewall allows SSH only, never 80/443 — the tunnel
dials outbound. SSH password-auth disable is guarded to avoid an unrecoverable lockout.
`deploy.sh` uses `tar | ssh`, not rsync (Git Bash ships ssh/tar but not rsync). Never
transfers `.env`. Not yet deployed — provisioning needs the user's own
payment/credentials.

### Deploy-script gotchas fixed 2026-08-12 (all found by targeting Oracle Cloud)

Both scripts now take `TOPOLOGY=cloudflare|tailscale` (default `cloudflare`).

- **`server-setup.sh` assumed root login and root's `authorized_keys`.** Oracle Cloud and AWS
  disable root SSH, so it must be run as `ssh ubuntu@IP 'sudo bash -s'`. Worse: Oracle *does*
  ship `/root/.ssh/authorized_keys`, but every entry is a `command=` forced command that only
  prints "please login as the user ubuntu". The old code copied that to the `leadlens` user,
  producing an account that authenticates and then refuses to do anything — which looks
  exactly like a broken deploy script. Key discovery now prefers `$SUDO_USER`'s keys and
  filters `command=` lines; verified against a simulated Oracle layout. The SSH-hardening
  guard keyed off the same wrong path, so password auth was silently left enabled too.
- **`deploy.sh` ran `docker compose up -d --build` with no service filter,** which starts
  `cloudflared` too. In the Tailscale topology there's no token, so it restart-loops forever
  and buries the app's logs. Now uses per-topology compose files + service list, and every
  later `docker compose` call (health wait, `ps`, `logs`) carries the same `-f` flags.
- **`deploy.sh` now refuses to deploy** a Tailscale topology whose `.env` lacks
  `LEADLENS_TAILSCALE_AUTH`, since an inert gate is invisible from the outside — the dashboard
  looks identical whether or not everyone can delete leads.
- Oracle's `netfilter-persistent` rules coexist with ufw rather than being replaced. They only
  ever deny more, and nothing inbound is needed, so the script flags them and leaves them
  alone — rewriting a vendor's firewall over SSH is how people lock themselves out.

### Hosting choice (decided 2026-08-12)

Requirement was **free, always-on, 2–3 users**. That eliminated nearly everything:

- **Vercel** — serverless, read-only FS, no persistent disk; the app *is* a 147 MB SQLite file
  written on every edit. Would need a Postgres rewrite first, and function timeouts kill
  `train_models()` (~18s per run). Also autoscaling N instances corrupts
  a single SQLite file — `Dockerfile` pins `--workers 1` for the same reason.
- **Hugging Face Spaces** — stateless ML demos; persistent storage is paid, and auth is HF
  accounts, not company identity. Would put 2,707 PII lead records on a public-by-default ML
  host with no DPA.
- **Render/Railway/Fly free** — Render spins down at 15 min idle *and* has an ephemeral disk,
  so the database is destroyed on restart. Others are trial-credit only.
- **Google Cloud free e2-micro** — 1 GB RAM will OOM the retrain (compose limit is 2 GB), and
  US-only regions.
- **Oracle Cloud Always Free ARM** (`VM.Standard.A1.Flex`, 4 OCPU / 24 GB) — was the pick until
  **Sakda's card was rejected at Oracle signup (2026-08-12)**. Still documented as route B in
  the runbook in case a card works later, with its two real caveats: ARM "out of host capacity"
  is common, and Oracle reclaims instances idle under ~20% for 7 days.
- **Also card-blocked:** GCP / AWS / Azure free tiers all require one. Card-*free* PaaS was
  checked and all of it fails on state — Render free has an ephemeral disk **and** 15-min
  spindown (destroys the DB), HF Spaces free has no persistent disk, PythonAnywhere free is
  WSGI-only so FastAPI won't run at all.

**Chosen 2026-08-12: an always-on Windows PC at the office** (`deploy/RUNBOOK-TAILSCALE-WINDOWS.md`)
+ Tailscale. No new hardware, no OS reinstall, no card. Docker Desktop runs the identical Linux
container, so nothing about the app changes — only the host and the start-up path do. Deployment
is `git clone` + `git pull` from the GitHub remote rather than `deploy.sh`'s tar-over-ssh, which
is simpler than the Linux route.

`deploy/deploy-windows.ps1` is the counterpart to `deploy.sh`: PowerShell 5.1-compatible (no
ternary/`??`/`&&`), and it exists mainly to carry over the `LEADLENS_TAILSCALE_AUTH` refusal —
without that guard an unset flag silently means "no gate at all" and the dashboard looks
identical. It also brings up `app` only (base compose defines `cloudflared`, which would
restart-loop tokenless) and then reads the app's own log back to confirm
`Tailscale identity enforced` rather than trusting the `.env` check alone. Its guard regex was
tested against 8 `.env` variants and matches `_flag()`'s truthiness (`1|true|yes|on`).

**Docker Desktop only runs inside a signed-in Windows session**, which conflicts with the actual
requirement (24/7, unattended). Tailscale is a service and survives reboots on its own; Docker
Desktop does not, so a 3am Windows Update reboot leaves the app down at the login screen until
someone walks over. Hence a **second startup mode, now the recommended one:**

**Option A — native Windows startup task, no Docker** (`deploy/install-windows-service.ps1` +
`deploy/run-windows.ps1`). Registers a task named `LeadLens` running **at startup as SYSTEM**, so
it needs no sign-in and no stored password; restarts on failure; `ExecutionTimeLimit 0` because
the default 3 days would silently kill it. Runs the same `uvicorn ... --host 127.0.0.1 --workers 1`
used in dev, so this is low-risk. Installer also creates the venv, pip-installs, and builds
`frontend/dist` (gitignored, so a fresh clone lacks it — without it the API works but the
dashboard 404s). Needs Python + Node on that PC instead of Docker Desktop; lighter, no WSL2.

**The trap that made `run-windows.ps1` necessary: `env_file: .env` is a Compose feature.** Nothing
outside Compose reads `.env`, and `auth.py` is plain `os.getenv` — so running uvicorn directly
would start with `LEADLENS_TAILSCALE_AUTH` unset, i.e. **no gate at all**, looking completely
normal. The wrapper loads `.env` itself (splitting on the **first** `=` only, since a CF tunnel
token is base64 and ends in `=` padding) and refuses to start if neither gate is configured.
Mirrors `config.mode`: Cloudflare needs *both* vars. Tested against 8 `.env` variants.

**Option B — Docker Desktop** is still documented, for container isolation, but requires `Win+L`
rather than sign-out and either accepting manual restarts or `netplwiz` auto-sign-in (physical-
access trade-off spelled out in the runbook). Sleep is the other classic killer on Windows, hence
the `powercfg /change standby-timeout-ac 0` step.

**Backups differ by mode.** `backup.sh` drives `backup.py` through a throwaway container, which
does not exist in Option A — so `deploy/backup-windows.ps1` calls `backup.py`/`restore.py`
directly through the venv. Same encryption, same format, same immediate verify-after-write.
**Tested end-to-end against the real 140 MB database: 36 MB encrypted output, integrity check
passed with row counts (3,020 lead_events, 14,012 forecasts), `KEEP_LOCAL` pruning correct.** Its
Task Scheduler entry can run as SYSTEM ("whether user is logged on or not"); Option B's cannot,
because Docker needs the session. `logs/` and `backups/` are gitignored — the log carries the
email addresses auth accepts and rejects.

**Ubuntu remains supported and documented** (`deploy/RUNBOOK-TAILSCALE.md`) for a mini PC /
spare desktop / Pi 4+ on an SSD, if hardware turns up later. The existing scripts work
unchanged there — `server-setup.sh` is plain Ubuntu/apt, and Tailscale's outbound-only
connection means no port forwarding, no static IP, and CGNAT doesn't matter.
Side benefit: resolves the data-residency concern the Cloudflare runbook raises, since the PII
stays on hardware in-country. New failure modes are physical — power cuts (enable BIOS "Restore
on AC Power Loss"; `restart: unless-stopped` covers the container), lid-close suspend on a
laptop, and office-ISP outages. If no spare hardware turns up, Hetzner/DO/Vultr all take
**PayPal**, which often works when a local card fails 3-D Secure.

## Public demo deploy (2026-08-14)

Separate from the live business deployment above: a second, disposable deploy exists purely
as a public-facing demo of the app itself, with **no real customer data ever loaded into it**.

- Attempted first on **Hugging Face Spaces** — blocked: Docker Spaces (private or public) now
  require a Pro subscription even on `cpu-basic`, which used to be free. Not pursued further.
- Landed on **Render**, deployed straight from a second GitHub remote
  (`github.com/Sakda2003/Explorer-by-SL-Leads`, kept separate from the primary `origin` remote
  pointing at `Sakda101/...`) using the existing root `Dockerfile` unmodified — free web
  service, port 8000, health check `/api/health`.
- Live at `https://explorer-by-sl-leads.onrender.com/` (first successful deploy 2026-08-14).
- Same storage caveat as the table above: Render free tier has an **ephemeral disk**, so the
  SQLite DB resets on every restart/redeploy — and the free tier also spins down after ~15 min
  idle, so a *visit* after a quiet spell is enough to lose an import. The `VOLUME ["/data"]`
  line in the Dockerfile does **not** grant persistence on Render; disks there need a paid
  instance. Accepted deliberately since this deploy is a UI demo, not a data store. Anything
  needing to survive stays on the office-PC + Tailscale deployment.
- **Auth: `BASIC_AUTH_USER`/`BASIC_AUTH_PASS`**, set as Render environment variables (never
  committed) — see the "Three topologies" section above for why Cloudflare/Tailscale mode
  can't apply here. Until those env vars are set, the deploy is unauthenticated; the URL must
  be treated as unlisted, not public, in that window.
- Render's **Health Check Path must be `/api/health`** — its default placeholder is `/healthz`,
  which this app does not serve, so leaving it would mark a healthy service as failing.

**Gotcha: an inert gate is invisible from the outside.** Verified 2026-08-14 that the first
live deploy answered `GET /api/dashboard/summary` with `200` and no `WWW-Authenticate` header —
i.e. `BASIC_AUTH_*` had not been saved in Render's Environment tab, so every route including the
DELETE ones was open to anyone with the URL. Same failure shape `deploy-windows.ps1` guards
against on the Tailscale side: the dashboard looks completely normal either way. **The check is
one curl** — a gated service returns `401` plus a challenge header:

```
curl -s -o /dev/null -w '%{http_code}\n' https://<host>/api/dashboard/summary   # expect 401
```

**Gotcha: a partial commit is invisible locally but fatal to the deploy.** The first Render
build died with `ImportError: cannot import name 'get_dataset_row_ids' from 'backend.core'`.
Cause: only `app.py`/`auth.py` were committed while the `core.py` half of that feature sat
uncommitted in the working tree — locally everything ran fine because the working tree had
both, so nothing surfaced until a clean checkout was built elsewhere. **Before pushing a
deploy, verify against the pushed commit, not the working tree**: `git archive HEAD | tar -x -C
<tmp>` then import `backend.app` from that directory. That reproduces exactly what the
container sees and catches this class of error in seconds.

**Backups go to Google Drive, not B2/R2** — both of those want a card too. Drive is 15 GB free
on the account Sakda already has, and `deploy/backup.sh` only ever passes `$RCLONE_REMOTE` to
rclone (verified), so this is a config change with no code edit. On a headless box, answer `n`
to rclone's browser prompt and use the `rclone authorize` command it prints from another
machine. Off-site backup matters *more* here than on a VPS: a machine on a shelf can be stolen
or flooded and there's no provider snapshot behind it.

**Phase 4 (backup/restore):** AES-256-GCM encryption in Python (`cryptography`),
key from scrypt with a per-backup random salt. Uses **SQLite's online backup API,
never a file copy** (a raw file copy in WAL mode can capture a torn database).
`backup.sh` verifies immediately after writing (decrypt + integrity check + row
counts) and aborts if that fails. The encryption passphrase must be stored off-server.

Related: [[Stack-and-Build]].
