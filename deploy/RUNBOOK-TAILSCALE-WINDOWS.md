# Deploying LeadLens on an always-on Windows PC

End state: `https://leadlens.<your-tailnet>.ts.net` works for up to 3 people, from anywhere,
without your laptop being open. Runs on a Windows PC that is already switched on at the office.
**Free, no domain, no card, no new hardware, no reinstalling Windows.**

This is the Windows variant of [RUNBOOK-TAILSCALE.md](RUNBOOK-TAILSCALE.md). Same security
model, same `.env`, same app — only the host differs. Docker runs the identical Linux container
either way, so nothing about the application changes.

Read the **one hard limitation** in step 6 before you commit to this route. It is not a
dealbreaker, but it is the thing that will bite you if you do not plan for it.

## What the PC needs

- **Windows 10 (64-bit, build 19045+) or Windows 11**, with virtualisation enabled in the BIOS
  (needed by WSL2 — nearly all office PCs have it).
- **8 GB RAM** comfortable. Docker Desktop plus WSL2 have overhead on top of the container's
  2 GB cap; 4 GB will work but will feel tight.
- **~10 GB free disk** for Docker images plus the database.
- A PC that is **already left on** — reception, accounting, a shared workstation. This works
  best when it is not someone's personal machine that they shut down at 6pm.

---

## 1. Prepare the PC  *(you, at the machine)*

**Stop it going to sleep.** This is the most common reason a self-hosted app "randomly" goes
offline. In an **Administrator** PowerShell:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
powercfg /change monitor-timeout-ac 15
```

The last line lets the *screen* sleep, which is fine and saves power — only the machine itself
must stay awake. Confirm with `powercfg /query SCHEME_CURRENT` if you want to check.

Also worth setting now:

- **BIOS → "Restore on AC Power Loss"** (or "AC Back" / "After Power Failure → Power On"), so a
  power cut does not leave the PC off until someone presses the button.
- **Windows Update active hours** (Settings → Windows Update → Advanced) so a forced restart
  lands outside working hours. Updates will still restart the PC — step 6 covers that.

**Install what you need.** From an **Administrator** PowerShell:

```powershell
winget install --id Git.Git -e
winget install --id tailscale.tailscale -e
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
```

Close and reopen PowerShell afterwards so `git`, `python` and `npm` land on `PATH`.

That list is for the **recommended startup mode** in step 5 (a Windows startup task — no Docker
at all). Node is needed only to build the dashboard bundle once; if you would rather not install
it, you can build `frontend\dist` on your laptop and copy the folder over, and step 5 will tell
you so.

If you instead choose the Docker option in step 5, swap Python and Node for
`winget install --id Docker.DockerDesktop -e`, launch it once until it says *Engine running*,
and tick **Settings → General → "Start Docker Desktop when you sign in"**.

> Docker Desktop's installer may want a reboot, and on some machines it silently does a
> **per-user** install into `%LOCALAPPDATA%\Programs\DockerDesktop` instead of `C:\Program
> Files` when the shell was not elevated. If `docker info` is not found afterwards, that is
> usually why — reinstall from an elevated prompt.

### What comes from GitHub, and what does not

`git clone` in step 2 brings every file the app needs. Two things are deliberately excluded and
have to be put on this PC by hand — both are one-time:

| | How it gets there | Why it is not in git |
|---|---|---|
| Application code | `git clone`, then `git pull` to update | — |
| **`.env`** | You type it, step 4 | Holds the allowlist; secrets do not belong in a repo |
| **The database** | You copy it once, step 9 | 147 MB of customer PII, and git history is permanent |

**Until you do step 9, the dashboard will be empty.** That is expected, not a broken deploy —
your existing leads live on your laptop and nothing moves them automatically. Get an empty
dashboard loading over the tailnet first, then migrate.

## 2. Get the code  *(you, at the machine)*

```powershell
mkdir C:\leadlens
cd C:\leadlens
git clone https://github.com/Sakda101/Explorer-by-SL-Leads.git
cd Explorer-by-SL-Leads
```

A private repo will prompt for a GitHub sign-in; Git Credential Manager handles it in a
browser. From here on, shipping a change is `git pull` — no upload step, no SSH.

## 3. Join the tailnet and publish the app  *(you, at the machine)*

In an **Administrator** PowerShell:

```powershell
tailscale up
```

It prints a URL — open it and sign in. **Sign in as yourself, with the same account you use on
your own laptop.** That is correct and intended: Tailscale counts *users* and *devices*
separately (3 users / 100 devices on the free plan), so this PC becomes another **device you
own**, not a second user. Your three seats are you plus two colleagues.

> **One consequence to be aware of.** Because this PC is signed in as you, and you are in
> `LEADLENS_WRITER_EMAILS`, anyone who sits at it and opens the dashboard has *your* write
> access — including deleting leads. The Option A startup mode in step 5 closes this: the app
> runs as SYSTEM at boot, so **nobody needs to be signed in to Windows at all** and you can
> leave the machine at the lock screen. Option B cannot, since Docker Desktop requires a live
> session. If this is a shared reception-type PC, that difference matters.

Then in the Tailscale admin console (<https://login.tailscale.com/admin>):

1. **DNS → MagicDNS:** enable it.
2. **DNS → HTTPS Certificates:** enable it. `tailscale serve` cannot issue its certificate
   without this and will fail in the next command.

Back in the Administrator PowerShell:

```powershell
tailscale set --hostname=leadlens
tailscale serve --bg 8000
tailscale serve status
```

`serve status` prints the URL your colleagues will use, e.g. `https://leadlens.tailXXXX.ts.net`.

Two things worth knowing:

- **Tailscale on Windows runs as a service**, so it comes back by itself after a reboot and the
  `serve` configuration persists. You do not need to re-run these commands.
- Serve terminates HTTPS on the tailnet and proxies to `127.0.0.1:8000`, which is exactly what
  `docker-compose.tailscale.yml` binds. It also stamps each request with the caller's tailnet
  identity in a `Tailscale-User-Login` header — that is what step 4 turns into the read/write
  split.

> **Never use `tailscale funnel`.** Funnel exposes a service to the *public internet* and its
> traffic carries no identity, so `backend/auth.py` will 401 everyone. That refusal is
> protecting a database of customer records — do not look for a way around it.

## 4. Create `.env`  *(you, at the machine)*

In `C:\leadlens\Explorer-by-SL-Leads`, create a file named exactly `.env`:

```powershell
notepad .env
```

```dotenv
LEADLENS_TAILSCALE_AUTH=1
LEADLENS_ALLOWED_EMAILS=you@yourcompany.com,colleague@yourcompany.com
LEADLENS_WRITER_EMAILS=you@yourcompany.com
LEADLENS_CORS_ORIGINS=
```

> Notepad likes to save as `.env.txt`. In the Save dialog set **"Save as type" → "All Files"**,
> or check afterwards with `dir .env` — if you see `.env.txt`, rename it.

`.env` is gitignored, so it never syncs to GitHub and has to be created on each machine.

Three things worth understanding:

- **`LEADLENS_TAILSCALE_AUTH=1` is not optional.** Without it the app has no gate at all and
  every tailnet member can delete leads and trigger retrains — with the dashboard looking
  identical either way. `deploy-windows.ps1` refuses to start in that state.
- **The emails must match each person's Tailscale login**, i.e. the account they sign in to
  Tailscale with (their Google address if the tailnet uses Google SSO) — not a work alias.
- **`LEADLENS_WRITER_EMAILS` listing only you** gives the others the dashboard without the
  ability to delete a lead or kick off a retrain by accident. Leave it blank to give everyone
  full access.

## 5. Start it — pick a startup mode  *(scripted)*

### Option A — Windows startup task  *(recommended: true 24/7, no Docker)*

In an **Administrator** PowerShell, from the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\install-windows-service.ps1
```

Creates the virtualenv, installs the Python dependencies, builds `frontend\dist`, registers a
task named **LeadLens** that runs **at startup as SYSTEM**, starts it, and waits for
`http://127.0.0.1:8000/api/health` to answer.

**Why this is the recommendation:** the task has no dependency on anyone being signed in. A
Windows Update reboot at 3am, or a power cut at the weekend, and the app comes back by itself
with the office empty. Tailscale is already a service, so the whole stack self-heals. It is also
lighter — no Docker Desktop, no WSL2 — and it is the same `uvicorn` command used in development.

Re-run the same script after a `git pull` to reinstall dependencies, rebuild the bundle and
re-register the task.

> **One thing this script protects you from.** `env_file: .env` is a *Docker Compose* feature —
> Compose reads that file and injects the variables. Nothing outside Compose does, and
> `backend/auth.py` is plain `os.getenv`. So running `uvicorn` directly would start the app with
> `LEADLENS_TAILSCALE_AUTH` unset, i.e. **with no access gate at all**, looking completely
> normal. `deploy\run-windows.ps1` loads `.env` itself and refuses to start if no gate is
> configured. Do not bypass it by calling `uvicorn` straight from a task.

Managing it afterwards:

```powershell
Get-ScheduledTask -TaskName LeadLens | Get-ScheduledTaskInfo   # last run, last result
Restart-ScheduledTask -TaskName LeadLens
Stop-ScheduledTask -TaskName LeadLens
Get-Content logs\leadlens.log -Tail 40                          # app output
```

### Option B — Docker Desktop

```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy-windows.ps1
```

Refuses to start if `.env` is missing or `LEADLENS_TAILSCALE_AUTH` is not enabled, brings up the
`app` service only (the base compose file also defines `cloudflared`, which has no token here and
would restart-loop), waits for the health check, then reads the app's own log back to confirm it
printed **`Tailscale identity enforced`** rather than `No access gate is configured`.

Choose this if you would rather keep the container isolation, or the PC already runs Docker for
something else. **But read step 6 first** — it does not give you unattended 24/7.

## 6. Surviving reboots

**With Option A there is nothing to do.** The task is registered at startup as SYSTEM, Tailscale
is a service, and both come back with nobody signed in. Verify it for real once, now rather than
during a Windows Update: restart the PC, do **not** sign in, and open the dashboard from your own
laptop. If it loads, this is genuinely unattended.

**With Option B, Docker Desktop only runs inside a signed-in Windows session.** Tailscale
survives a reboot; Docker Desktop does not. So:

- **Do not sign out.** Sign in once, let Docker start, then lock the screen with `Win+L` —
  locking keeps the session and the container alive. Signing out stops Docker.
- **After a reboot nothing runs until someone signs in.** Either accept that (the app returns
  the moment anyone signs in to that PC), or enable automatic sign-in with `netplwiz` — untick
  "Users must enter a user name and password to use this computer".

> If you enable auto sign-in, understand the trade: anyone who can physically reach that PC
> lands in a live Windows session. Prefer a dedicated low-privilege local account over someone's
> day-to-day login. It does **not** weaken the app's own gate — Tailscale plus
> `LEADLENS_ALLOWED_EMAILS` still apply to anyone opening the dashboard. Option A avoids this
> trade-off entirely, which is the main reason it is recommended.

## 7. Invite your colleagues  *(you)*

Tailscale admin console → **Users → Invite users**, one invite per person.

**The free plan is 3 users total, including you.**

### What each person does on their own device

Nothing technical, and **nothing about this project** — no Docker, no Git, no code, no copy of
the database. Their machine is only a browser talking to yours. Send them this:

> 1. Accept the Tailscale invite in your email.
> 2. Install Tailscale: <https://tailscale.com/download> (Windows, Mac, iPhone, Android).
> 3. Sign in **with the same account the invite was sent to**. This is the part that matters —
>    if you sign in with a different account, the dashboard will load a permission error.
> 4. Check the Tailscale icon shows *Connected*.
> 5. Open **`https://leadlens.<your-tailnet>.ts.net`** and bookmark it.

Fill in the real URL from `tailscale serve status` before sending — they cannot discover it.

Things worth telling them up front, because each one otherwise generates a support question:

- **Tailscale has to be connected to open the dashboard.** Outside the tailnet the address does
  not resolve at all — it will look like the site does not exist rather than like a login error.
- **It is not a normal VPN.** It does not route their web browsing or slow their connection;
  it only makes tailnet machines reachable. They can leave it on permanently.
- **The certificate is real**, so no browser warnings. If they see one, something is wrong —
  tell you rather than clicking through.
- **Phones work**, same URL, same sign-in.
- **They may see read-only behaviour**, by design, if they are not in `LEADLENS_WRITER_EMAILS`:
  the dashboard is fully visible but editing or deleting a lead returns *"This account has
  read-only access."* Tell them that is intentional, not a bug.
- **It is only up while the office PC is on and signed in** (step 6). If the dashboard is
  unreachable, that PC is the first thing to check, not their own machine.

**Install Tailscale on your own laptop too**, signed in as the same account. That is how you use
and administer the app without walking over to the office PC.

**Your laptop is not part of serving the app.** Once the office PC is running, you can close your
laptop, shut it down, or take it home with no effect on anyone else's access — the office PC is
the server and your laptop is just another viewer. The only thing you need it for afterwards is
shipping code changes: `git push` from the laptop, `git pull` on the office PC.

## 8. Verify  *(do all five)*

1. On the office PC, open the URL from step 3 → dashboard loads.
2. From your own laptop with Tailscale installed → dashboard loads.
3. From a device **not** on the tailnet → does not resolve or connect.
4. Not exposed on the office LAN. From another PC on the same network, with `OFFICE_PC_IP` as
   the office machine's LAN address:

   ```powershell
   curl.exe -m 5 http://OFFICE_PC_IP:8000/
   ```

   Must fail to connect. The compose override binds `127.0.0.1`, so this stays shut even though
   Windows Firewall is untouched. If it answers, you started the app without
   `docker-compose.tailscale.yml` — re-run `deploy-windows.ps1`.
5. If `LEADLENS_WRITER_EMAILS` lists only you, have a colleague confirm the dashboard loads and
   that deleting a lead returns *"This account has read-only access."*

## 9. Move your existing database over  *(once)*

The container starts with an empty database. To carry over the ~147 MB one from your laptop:

**On your laptop**, take a proper snapshot — `.backup` is safe on a live WAL database, a file
copy is not:

```powershell
.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('data/leadlens.db'); d=sqlite3.connect('leadlens-transfer.db'); s.backup(d); d.close(); s.close()"
```

Copy `leadlens-transfer.db` to the office PC (USB stick, network share, or Drive) into, say,
`C:\leadlens\transfer\`.

**If you chose Option A (startup task)** — the database is a plain file at `data\leadlens.db`
inside the repo folder, so this is just a copy:

```powershell
cd C:\leadlens\Explorer-by-SL-Leads
Stop-ScheduledTask -TaskName LeadLens
New-Item -ItemType Directory -Force data | Out-Null
Remove-Item data\leadlens.db-wal, data\leadlens.db-shm -ErrorAction SilentlyContinue
Copy-Item C:\leadlens\transfer\leadlens-transfer.db data\leadlens.db -Force
Start-ScheduledTask -TaskName LeadLens
```

**If you chose Option B (Docker)** — the database lives in a named volume, so it goes through a
throwaway container:

```powershell
$c = '-f','docker-compose.yml','-f','docker-compose.tailscale.yml'
docker compose @c stop app
docker compose @c run --rm --no-deps -v C:\leadlens\transfer:/xfer app sh -c "rm -f /data/leadlens.db-wal /data/leadlens.db-shm && cp /xfer/leadlens-transfer.db /data/leadlens.db"
docker compose @c start app
```

Removing the `-wal` and `-shm` sidecars matters in both cases: leaving a stale write-ahead log
next to a replaced database can make SQLite read the wrong state or refuse to open it.

Then delete `leadlens-transfer.db` from the USB stick and from both machines — it is an
unencrypted copy of every customer record.

## 10. Backups → Google Drive  *(do not skip)*

Follow **step 8 of [RUNBOOK.md](RUNBOOK.md)** for generating the passphrase and for the restore
drill. The encryption, the format and `restore.py` are identical here; only how the snapshot is
invoked differs. Generate and **store the passphrase in your password manager before going
further** — it is not recoverable, and a backup whose key lived only on the machine that died is
worthless.

**Install and configure rclone** (Administrator PowerShell):

```powershell
winget install --id Rclone.Rclone -e
rclone config
```

Choose `n` for new remote, name it `gdrive`, storage type `drive`, leave client id/secret blank,
scope `3` (its own folder only — enough, and tighter than full access), and authorise in the
browser. Then:

```powershell
rclone mkdir gdrive:leadlens-backups
rclone lsd gdrive:
```

Google Drive gives 15 GB free and needs no card, unlike Backblaze B2 or Cloudflare R2.

**Create `backup.env`** in `C:\leadlens\Explorer-by-SL-Leads` (gitignored, holds the key):

```dotenv
LEADLENS_BACKUP_PASSPHRASE=<generate one and store it in your password manager FIRST>
RCLONE_REMOTE=gdrive:leadlens-backups
KEEP_LOCAL=7
KEEP_REMOTE_DAYS=30
```

Add `BACKUP_DIR=C:\leadlens\backups` to that file too if you want the local copies outside the
repo folder; it defaults to `<repo>\backups`.

**Run it once by hand.** For **Option A** (startup task), from PowerShell in the repo folder:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\backup-windows.ps1
```

For **Option B** (Docker), the shell version drives the same `backup.py` through a throwaway
container — run it from **Git Bash**:

```bash
APP_DIR=. BACKUP_DIR=/c/leadlens/backups ./deploy/backup.sh
```

Either way it snapshots with SQLite's online backup API (never a file copy — in WAL mode the
newest committed rows can still be in `leadlens.db-wal`), then immediately decrypts and
integrity-checks what it just wrote and prints the row counts it found, aborting if any of that
fails. Expect roughly **36 MB** per backup from a 140 MB database, so 7 local copies is about
250 MB.

**Schedule it** with Task Scheduler → Create Task:

- **General:** for Option A, **"Run whether user is logged on or not"** with the **SYSTEM**
  account — it needs no session, same as the app. For Option B it must be "Run only when user
  is logged on", because Docker needs the session.
- **Triggers:** Daily, 03:15.
- **Actions:** Start a program
  - **Option A** — Program: `powershell.exe`
    Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\leadlens\Explorer-by-SL-Leads\deploy\backup-windows.ps1"`
  - **Option B** — Program: `C:\Program Files\Git\bin\bash.exe`
    Arguments: `-lc "cd /c/leadlens/Explorer-by-SL-Leads && APP_DIR=. BACKUP_DIR=/c/leadlens/backups ./deploy/backup.sh >> /c/leadlens/backup.log 2>&1"`

Check it ran: Task Scheduler shows *Last Run Result* `0x0`, and a new file appears in the backup
folder. Nothing tells you when backups silently **stop**, so skim that folder occasionally, or
add a healthchecks.io dead-man ping (free tier) to the end of the script.

> Backups are AES-256-GCM encrypted **before** they leave the machine, so Google never holds
> readable customer data — the passphrase is what protects them, which is why it must live in
> your password manager and not only on this PC. **This matters more here than on a VPS:** an
> office PC can be stolen, flooded, or have its disk die, and there is no provider snapshot
> behind it. Do the restore drill (RUNBOOK.md step 8f) once now, while nothing is wrong.

---

## Everyday operations

```powershell
cd C:\leadlens\Explorer-by-SL-Leads

# Ship a change -- Option A (startup task)
git pull
powershell -ExecutionPolicy Bypass -File deploy\install-windows-service.ps1   # as Administrator

# Ship a change -- Option B (Docker)
git pull
powershell -ExecutionPolicy Bypass -File deploy\deploy-windows.ps1

# Logs / restart / stop
$c = '-f','docker-compose.yml','-f','docker-compose.tailscale.yml'
docker compose @c logs -f app
docker compose @c restart app
docker compose @c stop app

# The URL, and who is on the tailnet
tailscale serve status
tailscale status
```

**Revoking someone:** remove them in the Tailscale admin console (**Users**), *and* remove them
from `LEADLENS_ALLOWED_EMAILS` in `.env`, then restart. The tailnet removal is the gate that
matters; the `.env` edit is the second one. Do both.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| **Option A:** offline after a reboot | Check `Get-ScheduledTask -TaskName LeadLens \| Get-ScheduledTaskInfo` and `logs\leadlens.log`. This should not happen — the task needs no sign-in |
| **Option A:** task runs but nothing serves | Read `logs\leadlens.log`. Most likely `.env` has no gate, so `run-windows.ps1` refused to start on purpose |
| **Option A:** dashboard 404s, API works | `frontend\dist` was never built. Re-run `install-windows-service.ps1`, or copy `dist` from your laptop |
| **Option B:** everything offline after a reboot | Nobody has signed in to Windows, so Docker Desktop never started — step 6 |
| Offline overnight, fine in the morning | The PC is sleeping. Re-run the `powercfg` commands in step 1 and confirm someone did not change the power plan |
| Offline after Windows Update | Update forced a restart; same as the reboot row. Set active hours |
| `deploy-windows.ps1` says docker is not responding | Docker Desktop not started, or still initialising. Wait for *Engine running* |
| Script refuses: `LEADLENS_TAILSCALE_AUTH is not enabled` | Working as designed — see step 4. Also check the file is `.env`, not `.env.txt` |
| App starts but everyone gets 401 | `tailscale serve` is not in front. `tailscale serve status` should mention 8000. Also the symptom if funnel was used |
| Everyone gets 403 | An email in `LEADLENS_ALLOWED_EMAILS` does not match that person's Tailscale login; the app log shows the rejected address |
| Colleague can delete leads | `LEADLENS_WRITER_EMAILS` is blank, or the gate is off — check the log for `Tailscale identity enforced` |
| URL does not resolve for a colleague | MagicDNS off, or they are not signed in to the tailnet |
| `cloudflared` restart-looping | Something started the full stack. Use `deploy-windows.ps1`, which starts `app` only |
| Retrain dies, exit code 137 | Container hit its 2 GB limit; raise it in `docker-compose.yml` |
| Slow for colleagues off-site | Expected — traffic leaves over your office upload link |
| WSL2 / virtualisation errors from Docker | Enable virtualisation in the BIOS; run `wsl --update` |
