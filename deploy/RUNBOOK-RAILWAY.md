# Runbook: Railway (public demo)

The disposable, public-facing demo of the app. **No real customer data is ever loaded into
it** — `LEADLENS_DEMO_MODE=1` makes that a technical control rather than a promise, by
disabling every import endpoint.

For the deployment that holds real leads, use `RUNBOOK-TAILSCALE-WINDOWS.md`. Basic Auth is
one shared password with no per-person identity; it is the right gate for an unlisted demo and
the wrong one for a database of customer records.

## What Railway requires that other hosts do not

Learned from two failed builds, 2026-08-16:

- **No `VOLUME` instruction.** The build is rejected at image validation — `dockerfile
  invalid: docker VOLUME at Line 70 is not supported, use Railway Volumes` — before any layer
  is built. Persistence comes from a Railway Volume attached to the service, never from the
  Dockerfile. The instruction is gone from ours.
- **Bind `$PORT`.** Railway assigns a port per service and routes to it. The `CMD` reads
  `${PORT:-8000}`, so Railway gets the injected port and compose/Render/local keep 8000.
- **`Dockerfile` at the repo root, capital D** — auto-detected. `railway.json` names it
  explicitly anyway, so a future rename fails loudly instead of silently switching to Railpack.
- **Build-time env vars need `ARG`.** Ours needs none; the app reads everything at runtime.
- **Volumes and non-root don't mix.** Per Railway's volume docs, "images that run as a
  non-root UID by default will have permissions issues"; their workaround is
  `RAILWAY_RUN_UID=0`. Our image runs as UID 10001 deliberately. See "If you attach a volume"
  below — the demo does not need one.

`railway.json` (repo root) carries the rest as config-as-code, validated against
`https://railway.com/railway.schema.json`: builder, health check path and timeout, restart
policy, `numReplicas: 1`, and `watchPatterns` so a Vault-only commit doesn't trigger a rebuild.

`numReplicas` stays at 1 on purpose, same reason the Dockerfile runs one uvicorn worker: the
app is a single SQLite file, and a second replica is a second writer contending for the same
lock with no benefit at demo traffic.

## First deploy

1. **Create the service** from the GitHub repo `Sakda2003/Explorer-by-SL-Leads`, branch
   `main`. Railway detects the Dockerfile and reads `railway.json`.

2. **Set the variables** — before the first deploy, not after. With none set the app now
   refuses to boot (see "Fail-closed" below), so an empty variable list is a red deploy.

   | variable | value |
   | --- | --- |
   | `BASIC_AUTH_USER` | your choice |
   | `BASIC_AUTH_PASS` | long random string from a password manager |
   | `LEADLENS_DEMO_MODE` | `1` |
   | `LEADLENS_CORS_ORIGINS` | *(empty)* — frontend is same-origin; empty disables the middleware |

   Do **not** set `PORT` (Railway injects it) or `LEADLENS_DATA_DIR` (the image sets `/data`).

   Ignore Railway's "Suggested Variables" panel apart from those. It greps the source for
   env-var names, so it offers `CF_ACCESS_*`, `LEADLENS_TAILSCALE_AUTH`, `CF_TUNNEL_TOKEN` and
   the two email lists — all of which belong to topologies that do not exist here. Delete
   those rows. `LEADLENS_TAILSCALE_AUTH` is the one to be careful with: it makes the app trust
   an identity *header*, which is only sound behind `tailscale serve`, and it outranks Basic
   Auth in mode selection. On a public URL it would hand access to anyone who sends the header.

3. **Deploy**, then watch the build log. A successful build ends with the image pushed and the
   deploy log showing `Uvicorn running on http://0.0.0.0:<port>`.

4. **Verify the gate is live** — this is the step that was skipped on Render 2026-08-14 and
   left every DELETE route open:

   ```bash
   curl -si https://<service>.up.railway.app/api/health | head -1
   ```

   `/api/health` is exempt and must return `200`. Then check that the dashboard is not:

   ```bash
   curl -si https://<service>.up.railway.app/ | head -1
   ```

   Must be `401`, with a `WWW-Authenticate: Basic` header. If it returns `200`, the gate is
   inert — stop and fix the variables before sharing the URL.

## Fail-closed

`require_gate_or_die()` (`backend/auth.py`) refuses to start when a public-host marker is
present and no gate is configured. Railway sets `RAILWAY_ENVIRONMENT`, which is on the marker
list as of 2026-08-16 — before that the gate was keyed on `RENDER` alone and would have been
silently inert here, which is the same class of incident it exists to prevent.

The failure is loud and names what it detected:

```
Refusing to start with no access gate on a deployment that requires one
(detected: RAILWAY_ENVIRONMENT). Set BASIC_AUTH_USER + BASIC_AUTH_PASS ...
```

If you see that in the deploy log, the build was fine and the variables are missing.

## Storage

Railway's filesystem is ephemeral without a volume, so the SQLite database resets on every
redeploy and restart. **Accepted for the demo** — it holds no data worth keeping, and
`LEADLENS_DEMO_MODE=1` blocks the imports that would create any.

### If you attach a volume

Only worth doing if you want demo state to survive a redeploy.

1. Service → Data → add a volume, mount path `/data` (matches `LEADLENS_DATA_DIR` in the image).
2. Set `RAILWAY_RUN_UID=0`. Without it the container runs as UID 10001 against a root-owned
   mount and SQLite fails with `unable to open database file`.

That second step gives up the non-root property the image is built around — the app would be
able to write to its own code. Fine for a demo with no real data; not a trade to make on the
business deployment.

To make the volume mandatory instead of optional, add `"requiredMountPath": "/data"` to the
`deploy` block of `railway.json`; the deploy then fails rather than silently running on
ephemeral disk. Deliberately left out of the committed config so the demo deploys without one.

## Redeploying

Railway rebuilds on push to `main` of the connected repo:

```bash
git push newgh <branch>:main
```

`watchPatterns` in `railway.json` limits rebuilds to commits touching `backend/`, `frontend/`,
`deploy/`, `requirements.txt`, `Dockerfile`, `.dockerignore` or `railway.json`. A commit that
only updates the Vault will not trigger one.
