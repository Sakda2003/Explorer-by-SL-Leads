# Start (or restart) LeadLens on a Windows host, behind Tailscale Serve.
#
#     powershell -ExecutionPolicy Bypass -File deploy\deploy-windows.ps1
#
# This is the Windows counterpart to deploy/deploy.sh. There is no SSH and no upload step --
# the code is already here, having been cloned from git -- so all this does is the part that
# matters: refuse to start in an unsafe configuration, then bring up the app and wait for it
# to be healthy.
#
# Why it exists rather than just documenting `docker compose up`: on the Linux path, deploy.sh
# blocks a deployment whose .env forgot LEADLENS_TAILSCALE_AUTH. Without that check the app
# still starts, still looks completely normal, and every person on the tailnet can delete leads
# and trigger retrains. That failure is invisible from the outside, so it needs to be caught
# here too.
#
# Written for Windows PowerShell 5.1 (the version that ships with Windows), so no ternaries,
# no ?? operator, and no && chaining.

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root '.env'
$ComposeArgs = @(
    '-f', (Join-Path $Root 'docker-compose.yml'),
    '-f', (Join-Path $Root 'docker-compose.tailscale.yml')
)

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Yellow }
function Write-Ok($msg)   { Write-Host "    ok $msg" -ForegroundColor Green }
function Die($msg) { Write-Host "`nERROR: $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------------------
Write-Step 'Preflight'

# Docker Desktop only runs inside a logged-in Windows session. If the machine rebooted and
# nobody signed in, this is the failure you get -- see RUNBOOK-TAILSCALE-WINDOWS.md step 6.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Die "Docker is not responding. Start Docker Desktop and wait for it to say 'Engine running'.`n       After a reboot, Docker Desktop does not start until someone signs in to Windows."
}
Write-Ok 'docker is running'

if (-not (Test-Path $EnvFile)) {
    Die "$EnvFile not found. Create it -- see RUNBOOK-TAILSCALE-WINDOWS.md step 4."
}

# The check this script exists for. Matches 1/true/yes/on so it agrees with _flag() in
# backend/auth.py rather than inventing its own idea of truthiness.
$authOn = Select-String -Path $EnvFile -Pattern '^\s*LEADLENS_TAILSCALE_AUTH\s*=\s*(1|true|yes|on)\s*$' -Quiet
if (-not $authOn) {
    Die @"
LEADLENS_TAILSCALE_AUTH is not enabled in .env.

Without it the app has NO access gate: everyone on your tailnet can delete leads and
trigger retrains, and the dashboard looks exactly the same either way.

Add this line to $EnvFile and re-run:

    LEADLENS_TAILSCALE_AUTH=1

See deploy/RUNBOOK-TAILSCALE-WINDOWS.md step 4.
"@
}
Write-Ok 'LEADLENS_TAILSCALE_AUTH is enabled'

# Not fatal: Tailscale may legitimately not be set up yet on a first run. Worth saying though,
# because without Serve in front, every request arrives with no identity header and 401s.
$tailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (Test-Path $tailscaleExe) {
    $serve = & $tailscaleExe serve status 2>&1 | Out-String
    if ($serve -match '8000') {
        Write-Ok 'tailscale serve is pointing at port 8000'
    } else {
        Write-Host "    NOTE: tailscale serve does not mention port 8000 yet." -ForegroundColor DarkYellow
        Write-Host "          Until it does, every request 401s. Run (as Administrator):" -ForegroundColor DarkYellow
        Write-Host "              tailscale serve --bg 8000" -ForegroundColor DarkYellow
    }
} else {
    Write-Host "    NOTE: Tailscale not found. See RUNBOOK-TAILSCALE-WINDOWS.md step 3." -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------------------------------
Write-Step 'Building and starting'
# `app` only, deliberately. The base compose file also defines cloudflared, which has no token
# in this topology and would restart-loop forever, burying the app's own logs.
& docker compose @ComposeArgs up -d --build app
if ($LASTEXITCODE -ne 0) { Die 'docker compose up failed' }

# ---------------------------------------------------------------------------------------
Write-Step 'Waiting for health'
$containerId = (& docker compose @ComposeArgs ps -q app | Select-Object -First 1)
if (-not $containerId) { Die 'could not find the app container' }

$healthy = $false
foreach ($i in 1..60) {
    $status = (& docker inspect -f '{{.State.Health.Status}}' $containerId 2>$null | Out-String).Trim()
    if ($status -eq 'healthy')   { $healthy = $true; break }
    if ($status -eq 'unhealthy') {
        & docker compose @ComposeArgs logs --tail 40 app
        Die 'container reported unhealthy'
    }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    & docker compose @ComposeArgs logs --tail 40 app
    Die 'timed out waiting for the health check'
}
Write-Ok 'healthy'

# ---------------------------------------------------------------------------------------
# Confirm from the app's own logs which gate is actually in force. The .env check above proves
# the file says the right thing; this proves the running process agreed.
Write-Step 'Access gate'
$logs = (& docker compose @ComposeArgs logs app 2>&1 | Out-String)
if ($logs -match 'Tailscale identity enforced') {
    Write-Ok 'Tailscale identity enforced'
} elseif ($logs -match 'No access gate is configured') {
    Die 'the app started with NO access gate. Check .env and re-run.'
} else {
    Write-Host '    could not find the gate line in the logs; check manually:' -ForegroundColor DarkYellow
    Write-Host '        docker compose -f docker-compose.yml -f docker-compose.tailscale.yml logs app | findstr /i tailscale' -ForegroundColor DarkYellow
}

Write-Host @"

  Running. Reachable only through 'tailscale serve', and only for the emails in
  LEADLENS_ALLOWED_EMAILS.

  URL:      tailscale serve status
  Logs:     docker compose $($ComposeArgs -join ' ') logs -f app
  Restart:  docker compose $($ComposeArgs -join ' ') restart app

"@ -ForegroundColor Cyan
