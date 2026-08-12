#!/usr/bin/env bash
#
# Ship the current working tree to the server and restart the stack.
#
#     ./deploy/deploy.sh leadlens@203.0.113.10                      # Cloudflare tunnel
#     TOPOLOGY=tailscale ./deploy/deploy.sh leadlens@100.x.y.z      # Tailscale Serve
#
# Uses tar over ssh rather than rsync, because Git Bash on Windows ships ssh and tar but not
# rsync. Nothing here needs to run on the server beyond docker.
#
# Note what is NOT sent: .env stays on the server only. Secrets should live in exactly one
# place, and syncing them from a laptop is how they end up in a backup or a git history.

set -euo pipefail

TARGET="${1:-}"
APP_DIR="${APP_DIR:-/opt/leadlens}"
TOPOLOGY="${TOPOLOGY:-cloudflare}"

if [[ -z "$TARGET" ]]; then
  echo "usage: $0 user@host        e.g. $0 leadlens@203.0.113.10" >&2
  echo "       TOPOLOGY=tailscale $0 user@host" >&2
  exit 1
fi

# The two topologies bring up different services, and getting this wrong is not a silent
# mistake in either direction:
#   cloudflare -> both services; cloudflared dials out to Cloudflare with CF_TUNNEL_TOKEN.
#   tailscale  -> `app` ONLY, plus the override that binds it to 127.0.0.1. Starting the full
#                 stack here would launch cloudflared with no token, which restart-loops
#                 forever and buries the app's own logs under its failures.
case "$TOPOLOGY" in
  cloudflare)
    COMPOSE_FILES="-f docker-compose.yml"
    SERVICES="" ;;
  tailscale)
    COMPOSE_FILES="-f docker-compose.yml -f docker-compose.tailscale.yml"
    SERVICES="app" ;;
  *)
    echo "TOPOLOGY must be 'cloudflare' or 'tailscale', got '$TOPOLOGY'" >&2
    exit 1 ;;
esac

# Git Bash mangles POSIX-looking arguments into Windows paths; this keeps remote paths intact.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

log "Preflight ($TOPOLOGY)"
for f in Dockerfile docker-compose.yml requirements.txt backend frontend; do
  [[ -e "$f" ]] || { echo "missing $f -- run this from the repo, not $ROOT" >&2; exit 1; }
done
if [[ "$TOPOLOGY" == "tailscale" ]]; then
  [[ -e docker-compose.tailscale.yml ]] \
    || { echo "missing docker-compose.tailscale.yml -- required for TOPOLOGY=tailscale" >&2; exit 1; }
fi
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" "command -v docker >/dev/null" \
  || { echo "docker not found on $TARGET -- run deploy/server-setup.sh first" >&2; exit 1; }
ssh "$TARGET" "test -f $APP_DIR/.env" \
  || { echo "$APP_DIR/.env missing on server -- see deploy/RUNBOOK.md step 5" >&2; exit 1; }

# In the Tailscale topology there is no edge gate at all: if LEADLENS_TAILSCALE_AUTH is unset,
# backend/auth.py goes inert and every tailnet member can delete leads and trigger retrains.
# That is invisible from the outside -- the dashboard looks identical -- so it is checked here
# rather than left to be discovered after someone erases a record.
if [[ "$TOPOLOGY" == "tailscale" ]]; then
  ssh "$TARGET" "grep -qE '^[[:space:]]*LEADLENS_TAILSCALE_AUTH[[:space:]]*=[[:space:]]*(1|true|yes|on)[[:space:]]*$' $APP_DIR/.env" \
    || { echo "LEADLENS_TAILSCALE_AUTH is not enabled in $APP_DIR/.env." >&2
         echo "Without it every tailnet user gets write access. See deploy/RUNBOOK-TAILSCALE.md step 4." >&2
         exit 1; }
fi
echo "    ok"

log "Uploading source to $TARGET:$APP_DIR"
# Excludes mirror .dockerignore. data/ is excluded emphatically: the server's database lives in
# a docker volume, and copying a laptop copy over it would overwrite live customer records.
tar czf - \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./data' \
  --exclude='./frontend/node_modules' \
  --exclude='./frontend/dist' \
  --exclude='./.env' \
  --exclude='./Assets' \
  --exclude='./Dataset' \
  --exclude='./design-system' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  . | ssh "$TARGET" "tar xzf - -C $APP_DIR"
echo "    uploaded"

log "Building and starting"
ssh "$TARGET" "cd $APP_DIR && docker compose $COMPOSE_FILES up -d --build $SERVICES"

log "Waiting for health"
ssh "$TARGET" "
  cd $APP_DIR
  name=\$(docker compose $COMPOSE_FILES ps -q app)
  for i in \$(seq 1 60); do
    status=\$(docker inspect -f '{{.State.Health.Status}}' \$name 2>/dev/null || echo starting)
    [ \"\$status\" = healthy ] && { echo '    healthy'; exit 0; }
    [ \"\$status\" = unhealthy ] && { echo '    UNHEALTHY'; docker compose $COMPOSE_FILES logs --tail 40 app; exit 1; }
    sleep 2
  done
  echo '    timed out waiting for health'; docker compose $COMPOSE_FILES logs --tail 40 app; exit 1
"

log "Status"
ssh "$TARGET" "cd $APP_DIR && docker compose $COMPOSE_FILES ps"

if [[ "$TOPOLOGY" == "tailscale" ]]; then
cat <<EOF

  Deployed. The app listens on 127.0.0.1:8000 only and is reachable solely through
  \`tailscale serve\` -- so only devices signed into your tailnet, and only the emails in
  LEADLENS_ALLOWED_EMAILS.

  URL:      ssh $TARGET 'tailscale serve status'
  Logs:     ssh $TARGET 'cd $APP_DIR && docker compose $COMPOSE_FILES logs -f app'
  Restart:  ssh $TARGET 'cd $APP_DIR && docker compose $COMPOSE_FILES restart app'
EOF
else
cat <<EOF

  Deployed. The app is NOT reachable from the internet directly -- only through the
  Cloudflare tunnel, and only for the emails in your Access policy.

  Logs:     ssh $TARGET 'cd $APP_DIR && docker compose logs -f'
  Restart:  ssh $TARGET 'cd $APP_DIR && docker compose restart'
EOF
fi
