# Nightly backup for the native Windows deployment (startup-task mode, no Docker).
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File deploy\backup-windows.ps1
#
# The counterpart to deploy/backup.sh, which drives the same backup.py / restore.py through a
# throwaway container. With the startup-task deployment there is no container and the database is
# a plain file, so this calls them directly through the repo's virtualenv.
#
# Config comes from backup.env in the repo root (gitignored -- it holds the encryption key):
#
#     LEADLENS_BACKUP_PASSPHRASE=...        required
#     RCLONE_REMOTE=gdrive:leadlens-backups optional but strongly advised
#     BACKUP_DIR=C:\leadlens\backups        optional, defaults to <repo>\backups
#     KEEP_LOCAL=7
#     KEEP_REMOTE_DAYS=30
#
# Verifying immediately after writing is the point of this script, not a nicety. A backup job
# that reports success for six months and produces an unreadable file on the one day you need it
# is worse than having none, because you stopped worrying about it.

$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Config = Join-Path $Root 'backup.env'
$Db     = Join-Path $Root 'data\leadlens.db'

function Log($m) { Write-Host "$(Get-Date -Format s)  $m" }
function Die($m) { Log "ERROR: $m"; exit 1 }

if (-not (Test-Path $Python)) { Die "no virtualenv at $Python" }
if (-not (Test-Path $Config)) { Die "missing $Config (see RUNBOOK-TAILSCALE-WINDOWS.md step 10)" }
if (-not (Test-Path $Db))     { Die "no database at $Db" }

# ---- config ----------------------------------------------------------------------------
$cfg = @{}
foreach ($line in Get-Content $Config) {
    $t = $line.Trim()
    if ($t -eq '' -or $t.StartsWith('#')) { continue }
    $i = $t.IndexOf('=')
    if ($i -lt 1) { continue }
    # First '=' only: the passphrase is base64 from `openssl rand -base64 32` and often ends in
    # '=' padding, which splitting on every '=' would truncate -- producing backups encrypted
    # under a key you cannot reproduce at restore time.
    $cfg[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
}

$passphrase = $cfg['LEADLENS_BACKUP_PASSPHRASE']
if ([string]::IsNullOrWhiteSpace($passphrase)) { Die "LEADLENS_BACKUP_PASSPHRASE not set in $Config" }

$backupDir = $cfg['BACKUP_DIR']
if ([string]::IsNullOrWhiteSpace($backupDir)) { $backupDir = Join-Path $Root 'backups' }
$remote  = $cfg['RCLONE_REMOTE']
$keepLocal = 7
if ($cfg['KEEP_LOCAL']) { $keepLocal = [int]$cfg['KEEP_LOCAL'] }
$keepRemoteDays = 30
if ($cfg['KEEP_REMOTE_DAYS']) { $keepRemoteDays = [int]$cfg['KEEP_REMOTE_DAYS'] }

if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Force $backupDir | Out-Null }

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss') + 'Z'
$name  = "leadlens-$stamp.db.gz.enc"
$out   = Join-Path $backupDir $name

$env:LEADLENS_BACKUP_PASSPHRASE = $passphrase

# ---- snapshot --------------------------------------------------------------------------
# backup.py uses SQLite's online backup API, never a file copy: in WAL mode the newest committed
# rows can still be sitting in leadlens.db-wal, so a copy can silently lose data or produce an
# unopenable file. Safe to run while the app is serving.
Log 'snapshotting'
& $Python (Join-Path $Root 'deploy\backup.py') $Db $out
if ($LASTEXITCODE -ne 0) { Die 'snapshot failed' }
if (-not (Test-Path $out) -or (Get-Item $out).Length -eq 0) { Die 'backup file is missing or empty' }

# ---- verify ----------------------------------------------------------------------------
Log 'verifying (decrypt + integrity_check)'
& $Python (Join-Path $Root 'deploy\restore.py') --check $out
if ($LASTEXITCODE -ne 0) { Die "verification FAILED -- the file just written cannot be restored" }

# ---- off-site --------------------------------------------------------------------------
if (-not [string]::IsNullOrWhiteSpace($remote)) {
    if (-not (Get-Command rclone -ErrorAction SilentlyContinue)) {
        Die "RCLONE_REMOTE is set but rclone is not on PATH"
    }
    Log "uploading to $remote"
    & rclone copy $out $remote --no-traverse
    if ($LASTEXITCODE -ne 0) { Die 'upload failed' }

    Log "pruning remote (older than ${keepRemoteDays}d)"
    & rclone delete $remote --min-age "${keepRemoteDays}d" --include 'leadlens-*.db.gz.enc'
    if ($LASTEXITCODE -ne 0) { Log 'WARNING: remote prune failed (the backup itself succeeded)' }
} else {
    Log 'WARNING: RCLONE_REMOTE not set -- this backup exists only on this PC.'
    Log '         A stolen or failed machine loses the database and every backup together.'
}

# ---- prune local -----------------------------------------------------------------------
Log "pruning local (keeping newest $keepLocal)"
Get-ChildItem $backupDir -Filter 'leadlens-*.db.gz.enc' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $keepLocal |
    ForEach-Object { Remove-Item $_.FullName -Force; Log "  removed $($_.Name)" }

$sizeMb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Log "done: $name ($sizeMb MB)"
