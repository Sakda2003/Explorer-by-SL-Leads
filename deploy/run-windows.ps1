# Start LeadLens natively on Windows. This is what the LeadLens scheduled task runs at boot.
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File deploy\run-windows.ps1
#
# Not usually run by hand -- deploy\install-windows-service.ps1 registers it to run at startup
# as SYSTEM, so the app comes back after a reboot with nobody signed in.
#
# ---------------------------------------------------------------------------------------
# Why this wrapper exists at all, rather than the task calling uvicorn directly:
#
# `env_file: .env` in docker-compose.yml is a *Compose* feature. Compose reads that file and
# injects the variables into the container. Nothing outside Compose does that -- Python does not
# read .env on its own, and backend/auth.py is plain os.getenv.
#
# So running uvicorn directly on Windows would start the app with LEADLENS_TAILSCALE_AUTH unset,
# which makes backend/auth.py inert: every endpoint open, including the delete routes, with the
# dashboard looking completely normal. This wrapper loads .env into the process environment and
# then refuses to start if no gate is configured, so that failure cannot happen quietly.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$EnvFile = Join-Path $Root '.env'
$Python  = Join-Path $Root '.venv\Scripts\python.exe'

function Die($msg) {
    # Written to the log the scheduled task redirects, so a boot-time failure is diagnosable
    # after the fact rather than vanishing.
    Write-Host "[$(Get-Date -Format s)] ERROR: $msg"
    exit 1
}

if (-not (Test-Path $Python))  { Die "no virtualenv at $Python -- run deploy\install-windows-service.ps1 first" }
if (-not (Test-Path $EnvFile)) { Die "no .env at $EnvFile -- see deploy\RUNBOOK-TAILSCALE-WINDOWS.md step 4" }

# ---- load .env -------------------------------------------------------------------------
# Split on the FIRST '=' only: values legitimately contain '=' (a Cloudflare tunnel token is
# base64 and routinely ends in padding), and splitting on every '=' would silently truncate it.
foreach ($line in Get-Content $EnvFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }

    $idx = $trimmed.IndexOf('=')
    if ($idx -lt 1) { continue }

    $key = $trimmed.Substring(0, $idx).Trim()
    $val = $trimmed.Substring($idx + 1).Trim()

    # Tolerate quoted values, since a hand-edited .env often has them.
    if ($val.Length -ge 2) {
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or
            ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
    }

    [Environment]::SetEnvironmentVariable($key, $val, 'Process')
}

# ---- refuse to run without a gate ------------------------------------------------------
# Mirrors config.mode in backend/auth.py: Cloudflare needs both vars, Tailscale needs the flag.
# Kept in sync with _flag()'s truthiness deliberately -- a mismatch here would mean this script
# approves a configuration the app then treats as "off".
$truthy = @('1', 'true', 'yes', 'on')
$tsOn = $truthy -contains ("$env:LEADLENS_TAILSCALE_AUTH".Trim().ToLower())
$cfOn = (-not [string]::IsNullOrWhiteSpace($env:CF_ACCESS_TEAM_DOMAIN)) -and
        (-not [string]::IsNullOrWhiteSpace($env:CF_ACCESS_AUD))

if (-not ($tsOn -or $cfOn)) {
    Die @"
no access gate is configured, refusing to start.

.env set neither LEADLENS_TAILSCALE_AUTH=1 nor both CF_ACCESS_* values, which leaves every
endpoint open -- including the routes that delete customer records -- while the dashboard
looks exactly the same.

Add this to ${EnvFile}:

    LEADLENS_TAILSCALE_AUTH=1

See deploy\RUNBOOK-TAILSCALE-WINDOWS.md step 4.
"@
}

# In production the dashboard is served from this same origin by StaticFiles, so CORS is not
# needed. Only default it when .env said nothing -- an explicit value there wins.
if ($null -eq $env:LEADLENS_CORS_ORIGINS) {
    [Environment]::SetEnvironmentVariable('LEADLENS_CORS_ORIGINS', '', 'Process')
}

Write-Host "[$(Get-Date -Format s)] starting uvicorn (gate: $(if ($cfOn) { 'cloudflare' } else { 'tailscale' }))"

# ---- run -------------------------------------------------------------------------------
# 127.0.0.1 on purpose, never 0.0.0.0: `tailscale serve` proxies to loopback, and binding all
# interfaces would expose an unauthenticated-by-header database to the whole office LAN.
#
# One worker, matching the Dockerfile: this is a single SQLite file and concurrent writers
# across processes would contend on the database lock for no benefit at three users.
& $Python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
exit $LASTEXITCODE
