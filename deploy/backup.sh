#!/usr/bin/env bash
#
# Nightly backup. Runs on the VPS from cron; see deploy/RUNBOOK.md.
#
#     /opt/leadlens/deploy/backup.sh
#
# Snapshots the database from inside a throwaway container (so it works whether or not the app
# is running), verifies the result immediately, ships it off-box, then prunes.
#
# Verifying right after writing is the point. A backup job that reports success for six months
# and produces an unreadable file on the day you need it is worse than no backup, because you
# stopped worrying about it.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/leadlens}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/leadlens}"
CONFIG="${CONFIG:-$APP_DIR/backup.env}"

export MSYS_NO_PATHCONV=1

log() { printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ -f "$CONFIG" ]] || die "missing $CONFIG (see RUNBOOK step 8)"
# shellcheck disable=SC1090
set -a; source "$CONFIG"; set +a

: "${LEADLENS_BACKUP_PASSPHRASE:?not set in $CONFIG}"
KEEP_LOCAL="${KEEP_LOCAL:-7}"
KEEP_REMOTE_DAYS="${KEEP_REMOTE_DAYS:-30}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"

cd "$APP_DIR" || die "no $APP_DIR"
mkdir -p "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="leadlens-${STAMP}.db.gz.enc"

# ---------------------------------------------------------------------------------------
log "snapshotting"
# `run --rm --no-deps` reuses the app service's volume definition, so the volume name is never
# hardcoded here. :ro on the data mount is not possible via the service definition, but the
# script only ever opens the database read-only anyway.
docker compose run --rm --no-deps \
  -e "LEADLENS_BACKUP_PASSPHRASE=$LEADLENS_BACKUP_PASSPHRASE" \
  -v "$BACKUP_DIR:/backups" \
  --entrypoint python \
  app /app/deploy/backup.py /data/leadlens.db "/backups/$NAME" \
  || die "snapshot failed"

[[ -s "$BACKUP_DIR/$NAME" ]] || die "backup file is missing or empty"

# ---------------------------------------------------------------------------------------
log "verifying (decrypt + integrity_check)"
docker compose run --rm --no-deps \
  -e "LEADLENS_BACKUP_PASSPHRASE=$LEADLENS_BACKUP_PASSPHRASE" \
  -v "$BACKUP_DIR:/backups" \
  --entrypoint python \
  app /app/deploy/restore.py --check "/backups/$NAME" \
  || die "backup verification FAILED -- the file just written cannot be restored"

# ---------------------------------------------------------------------------------------
if [[ -n "$RCLONE_REMOTE" ]]; then
  command -v rclone >/dev/null || die "RCLONE_REMOTE set but rclone is not installed"
  log "uploading to $RCLONE_REMOTE"
  rclone copy "$BACKUP_DIR/$NAME" "$RCLONE_REMOTE" --no-traverse \
    || die "upload failed"

  log "pruning remote (older than ${KEEP_REMOTE_DAYS}d)"
  rclone delete "$RCLONE_REMOTE" --min-age "${KEEP_REMOTE_DAYS}d" --include 'leadlens-*.db.gz.enc' \
    || log "WARNING: remote prune failed (backup itself succeeded)"
else
  log "WARNING: RCLONE_REMOTE not set -- this backup exists only on this server."
  log "         A disk or provider failure loses the database and every backup together."
fi

# ---------------------------------------------------------------------------------------
log "pruning local (keeping newest $KEEP_LOCAL)"
# shellcheck disable=SC2012
ls -1t "$BACKUP_DIR"/leadlens-*.db.gz.enc 2>/dev/null \
  | tail -n "+$((KEEP_LOCAL + 1))" \
  | while read -r old; do rm -f -- "$old"; log "  removed $(basename "$old")"; done

log "done: $NAME ($(du -h "$BACKUP_DIR/$NAME" | cut -f1))"
