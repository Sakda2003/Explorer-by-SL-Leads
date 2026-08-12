# One-time setup: make LeadLens start at Windows boot, with nobody signed in.
#
# Run once, from the repo root, in an ADMINISTRATOR PowerShell:
#     powershell -ExecutionPolicy Bypass -File deploy\install-windows-service.ps1
#
# Idempotent -- safe to re-run after a `git pull` to reinstall dependencies and rebuild the
# frontend. Re-running also re-registers the task, so it is the way to apply changes.
#
# ---------------------------------------------------------------------------------------
# Why a scheduled task rather than Docker Desktop:
#
# Docker Desktop only runs inside a signed-in Windows session. When Windows Update reboots this
# PC at 3am, Docker never starts and the app is down until somebody walks over and signs in.
# A scheduled task registered to SYSTEM with an "at startup" trigger has no such dependency, so
# the app is genuinely unattended. Tailscale already installs itself as a service, so once this
# is in place a reboot restores the whole stack with no human involved.
#
# Running as SYSTEM also avoids storing anyone's Windows password, which "run whether user is
# logged on or not" would otherwise require for a normal account.

$ErrorActionPreference = 'Stop'

$TaskName = 'LeadLens'
$Root     = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $Root 'deploy\run-windows.ps1'
$EnvFile  = Join-Path $Root '.env'
$Python   = Join-Path $Root '.venv\Scripts\python.exe'
$LogDir   = Join-Path $Root 'logs'

function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Yellow }
function Write-Ok($m)   { Write-Host "    ok $m" -ForegroundColor Green }
function Die($m) { Write-Host "`nERROR: $m" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------------------
Write-Step 'Preflight'

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Die "must run as Administrator (registering a SYSTEM task requires it).`n       Right-click PowerShell -> Run as administrator, then re-run."
}
Write-Ok 'running elevated'

if (-not (Test-Path $EnvFile)) {
    Die "$EnvFile not found. Create it first -- see deploy\RUNBOOK-TAILSCALE-WINDOWS.md step 4."
}

# Same check run-windows.ps1 makes at boot, done here too so the mistake surfaces now, during
# setup, rather than as a silently open dashboard later.
$authOn = Select-String -Path $EnvFile -Pattern '^\s*LEADLENS_TAILSCALE_AUTH\s*=\s*(1|true|yes|on)\s*$' -Quiet
$cfOn   = (Select-String -Path $EnvFile -Pattern '^\s*CF_ACCESS_TEAM_DOMAIN\s*=\s*\S' -Quiet) -and
          (Select-String -Path $EnvFile -Pattern '^\s*CF_ACCESS_AUD\s*=\s*\S' -Quiet)
if (-not ($authOn -or $cfOn)) {
    Die @"
.env configures no access gate.

Without LEADLENS_TAILSCALE_AUTH=1 the app runs with every endpoint open, including the
routes that delete customer records, and the dashboard looks identical either way.

Add to ${EnvFile}:

    LEADLENS_TAILSCALE_AUTH=1
"@
}
Write-Ok 'access gate is configured in .env'

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Die "python not found on PATH. Install Python 3.12: winget install --id Python.Python.3.12 -e" }
$pyVer = (& python --version 2>&1 | Out-String).Trim()
Write-Ok "$pyVer"

# ---------------------------------------------------------------------------------------
Write-Step 'Python dependencies'
if (-not (Test-Path $Python)) {
    & python -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { Die 'failed to create the virtualenv' }
    Write-Ok 'created .venv'
} else {
    Write-Ok '.venv already present'
}
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Die 'pip install failed' }
Write-Ok 'requirements installed'

# ---------------------------------------------------------------------------------------
Write-Step 'Frontend bundle'
# backend/app.py mounts ROOT/frontend/dist via StaticFiles, so without this the API works but
# the dashboard is a 404. dist/ is gitignored, so a fresh clone never has it.
$dist = Join-Path $Root 'frontend\dist\index.html'
if (Test-Path $dist) {
    Write-Ok 'frontend/dist already built (delete it to force a rebuild)'
} else {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) {
        Die @"
frontend/dist is missing and Node is not installed, so it cannot be built.

Either install Node and re-run:
    winget install --id OpenJS.NodeJS.LTS -e

Or build it on your laptop (cd frontend; pnpm build) and copy the resulting
frontend\dist folder to this PC at:
    $Root\frontend\dist
"@
    }
    Push-Location (Join-Path $Root 'frontend')
    try {
        & corepack enable
        # --frozen-lockfile matters: every dependency in package.json is declared "latest", so
        # without the lockfile pinning resolved versions this PC could build a different bundle
        # than the one that was tested.
        & corepack pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { Die 'pnpm install failed' }
        & corepack pnpm build
        if ($LASTEXITCODE -ne 0) { Die 'pnpm build failed' }
    } finally { Pop-Location }
    Write-Ok 'frontend built'
}

# ---------------------------------------------------------------------------------------
Write-Step 'Registering the startup task'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory $LogDir | Out-Null }

# cmd.exe wraps the call purely to get stdout/stderr into a log file. A scheduled task has
# nowhere to print, so without this a crash at boot leaves no evidence.
$logFile = Join-Path $LogDir 'leadlens.log'
$inner = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" >> `"$logFile`" 2>&1"

$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $inner" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

# ExecutionTimeLimit 0 = never kill it; the default is 3 days, which would take the app down
# without explanation. RestartCount/Interval cover a crash: the task is restarted rather than
# left dead until the next reboot.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Ok 'removed the previous task registration'
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'LeadLens forecasting dashboard. Serves 127.0.0.1:8000 for Tailscale Serve.' | Out-Null
Write-Ok "registered '$TaskName' (at startup, as SYSTEM)"

# ---------------------------------------------------------------------------------------
Write-Step 'Starting'
Start-ScheduledTask -TaskName $TaskName

$ok = $false
foreach ($i in 1..45) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
}

if (-not $ok) {
    Write-Host "`n    did not answer on 127.0.0.1:8000 within 90s. Last log lines:" -ForegroundColor Red
    if (Test-Path $logFile) { Get-Content $logFile -Tail 30 }
    Die "not healthy. Full log: $logFile"
}
Write-Ok 'answering on http://127.0.0.1:8000/api/health'

Write-Host @"

  LeadLens is running as a startup task and will come back by itself after a reboot,
  with nobody signed in.

  Next:  tailscale serve --bg 8000        (if not done already -- step 3)
         tailscale serve status           (prints the URL to share)

  Log:      $logFile
  Restart:  Restart-ScheduledTask -TaskName $TaskName
  Stop:     Stop-ScheduledTask -TaskName $TaskName
  Status:   Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo

"@ -ForegroundColor Cyan
