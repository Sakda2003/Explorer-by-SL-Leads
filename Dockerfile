# syntax=docker/dockerfile:1

# ---- Stage 1: build the dashboard ------------------------------------------------------
# Node is only needed to produce frontend/dist, so it stays out of the runtime image.
FROM node:24-slim AS frontend

WORKDIR /build

# pnpm is pinned to the version used locally rather than "whatever corepack resolves", so a
# rebuild months from now produces the same output.
RUN corepack enable && corepack prepare pnpm@11.16.0 --activate

# Manifest and lockfile first: this layer is cached until dependencies actually change, so
# editing App.tsx does not trigger a full reinstall.
COPY frontend/package.json frontend/pnpm-lock.yaml ./
# --frozen-lockfile is what makes the build reproducible. Every dependency in package.json is
# declared as "latest", so without the lockfile pinning resolved versions, two builds a week
# apart could ship different bundles. It also fails loudly if the manifest and lockfile drift.
RUN pnpm install --frozen-lockfile

COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/public ./public
COPY frontend/src ./src

# Runs `tsc --noEmit && vite build`, so a type error fails the image build rather than
# shipping a broken bundle.
RUN pnpm build


# ---- Stage 2: runtime ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE: no .pyc litter in a read-mostly layer.
# PYTHONUNBUFFERED: logs reach `docker logs` immediately instead of sitting in a buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Redirects the database, uploads, and previews to the mounted volume in one variable.
    # Setting only LEADLENS_DB_PATH would leave uploads/ and previews/ inside the container,
    # where a redeploy silently discards them.
    LEADLENS_DATA_DIR=/data

WORKDIR /app

# pandas, cryptography and friends all publish manylinux wheels for cp312, so no compiler is
# needed here. curl is for the healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
# Backup and restore ship inside the image so the container can snapshot its own volume with
# no extra tooling on the host. They need only `cryptography`, already present via pyjwt[crypto].
COPY deploy/backup.py deploy/restore.py ./deploy/
# ROOT in core.py is parents[1] of backend/, i.e. /app, and app.py mounts ROOT/frontend/dist.
# The build output therefore has to land at exactly this path.
COPY --from=frontend /build/dist ./frontend/dist

# Run unprivileged: a process holding customer records should not be able to write to its own
# code. /data is created and owned here so the named volume inherits that ownership the first
# time Docker populates it.
RUN useradd --system --create-home --uid 10001 leadlens \
 && mkdir -p /data \
 && chown -R leadlens:leadlens /data /app
USER leadlens

# Deliberately no volume declaration for /data here. It never granted persistence anywhere this
# app actually runs -- compose names its own `leadlens-data:/data` mount, and Render's free tier
# has no disk at all -- while Railway rejects that instruction outright and fails the image
# build before it starts. Persistence is the platform's job: a compose named volume, or a
# Railway Volume attached with mount path /data. The word itself is kept out of this file, in
# uppercase, so that a scan looking for the instruction cannot match prose explaining its
# absence. See deploy/RUNBOOK-RAILWAY.md.
EXPOSE 8000

# /api/health is deliberately exempt from the Access check in backend/auth.py, so this keeps
# working once authentication is switched on.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/api/health" || exit 1

# One worker on purpose. The app is a single SQLite file, and concurrent writers across
# processes would contend on the database lock for no benefit at two users.
# $PORT is honoured because Railway assigns one per service and routes to it; everywhere else
# (compose, Render, local) nothing sets it and the 8000 default applies. `exec` keeps uvicorn as
# PID 1 so it still receives SIGTERM directly -- without it the shell swallows the signal and
# the container is killed after the stop timeout instead of shutting down cleanly.
CMD ["sh", "-c", "exec uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
