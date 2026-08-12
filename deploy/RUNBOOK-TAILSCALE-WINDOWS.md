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

**Install three things.** From an Administrator PowerShell:

```powershell
winget install --id Git.Git -e
winget install --id Docker.DockerDesktop -e
winget install --id tailscale.tailscale -e
```

Then **launch Docker Desktop once** and wait for it to report *Engine running*. In its
**Settings → General**, tick **"Start Docker Desktop when you sign in"**.

> Docker Desktop's installer may want a reboot, and on some machines it silently does a
> **per-user** install into `%LOCALAPPDATA%\Programs\DockerDesktop` instead of `C:\Program
> Files` when the shell was not elevated. If `docker info` is not found afterwards, that is
> usually why — reinstall from an elevated prompt.

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

It prints a URL — open it and sign in. That creates your tailnet and adds this PC to it. Then
in the Tailscale admin console (<https://login.tailscale.com/admin>):

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

## 5. Start it  *(scripted)*

```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy-windows.ps1
```

This refuses to start if `.env` is missing or `LEADLENS_TAILSCALE_AUTH` is not enabled, brings
up the `app` service only (the base compose file also defines `cloudflared`, which has no token
here and would restart-loop), waits for the health check, then reads the app's own log back to
confirm it printed **`Tailscale identity enforced`** rather than `No access gate is configured`.

The first build takes several minutes — it compiles the frontend and installs Python wheels.
Later runs reuse cached layers.

## 6. Keep it running — read this part  *(the one real limitation)*

**Docker Desktop only runs inside a signed-in Windows session.** Tailscale is a service and
survives reboots on its own; Docker Desktop does not. This has two consequences:

- **Do not sign out.** Sign in once, let Docker start, then **lock the screen with `Win+L`**.
  Locking keeps the session — and the container — alive. Signing out stops Docker.
- **After a reboot, nothing runs until someone signs in.** Windows Update, a power cut, or a
  well-meaning colleague restarting the PC will take the app offline until then.

Pick one of these:

**Option 1 — accept it.** Fine if you or a colleague are in the office most days. The app comes
back the moment anyone signs in to that PC. Simplest, nothing to configure.

**Option 2 — enable automatic sign-in**, so a reboot restores everything unattended. Run
`netplwiz`, untick "Users must enter a user name and password to use this computer", and enter
the account's password once.

> **Understand the trade-off before doing this.** Auto sign-in means anyone who can physically
> reach that PC lands in a live Windows session. Only do it if the machine is somewhere you
> would already consider physically secure, and prefer a dedicated low-privilege local account
> over someone's day-to-day login. It does **not** weaken the app's own gate — Tailscale plus
> `LEADLENS_ALLOWED_EMAILS` still apply to anyone opening the dashboard.

Either way, once signed in Docker Desktop restarts the container by itself: `restart:
unless-stopped` is already set in `docker-compose.yml`.

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

**Install Tailscale on your own laptop too.** That is how you use and administer the app without
walking over to the office PC.

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
`C:\leadlens\transfer\`. Then **on the office PC**, in `C:\leadlens\Explorer-by-SL-Leads`:

```powershell
$c = '-f','docker-compose.yml','-f','docker-compose.tailscale.yml'
docker compose @c stop app
docker compose @c run --rm --no-deps -v C:\leadlens\transfer:/xfer app sh -c "rm -f /data/leadlens.db-wal /data/leadlens.db-shm && cp /xfer/leadlens-transfer.db /data/leadlens.db"
docker compose @c start app
```

Removing the `-wal` and `-shm` sidecars matters: leaving a stale write-ahead log next to a
replaced database can make SQLite read the wrong state or refuse to open it.

Then delete `leadlens-transfer.db` from the USB stick and from both machines — it is an
unencrypted copy of every customer record.

## 10. Backups → Google Drive  *(do not skip)*

`deploy/backup.sh` runs fine on Windows through **Git Bash** — it already sets
`MSYS_NO_PATHCONV=1` and uses only tools Git for Windows ships. Follow **step 8 of
[RUNBOOK.md](RUNBOOK.md)** for the passphrase and the restore drill; here is the Windows wiring.

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

**Run it once by hand**, from Git Bash in the repo folder:

```bash
APP_DIR=. BACKUP_DIR=/c/leadlens/backups ./deploy/backup.sh
```

It snapshots, immediately decrypts and integrity-checks what it just wrote, prints the row
counts it found, and aborts if any of that fails.

**Schedule it** with Task Scheduler → Create Task:

- **General:** "Run only when user is logged on" — required, because it needs Docker, which
  needs the session.
- **Triggers:** Daily, 03:15.
- **Actions:** Start a program
  - Program: `C:\Program Files\Git\bin\bash.exe`
  - Arguments: `-lc "cd /c/leadlens/Explorer-by-SL-Leads && APP_DIR=. BACKUP_DIR=/c/leadlens/backups ./deploy/backup.sh >> /c/leadlens/backup.log 2>&1"`

> Backups are AES-256-GCM encrypted **before** they leave the machine, so Google never holds
> readable customer data — the passphrase is what protects them, which is why it must live in
> your password manager and not only on this PC. **This matters more here than on a VPS:** an
> office PC can be stolen, flooded, or have its disk die, and there is no provider snapshot
> behind it. Do the restore drill (RUNBOOK.md step 8f) once now, while nothing is wrong.

---

## Everyday operations

```powershell
cd C:\leadlens\Explorer-by-SL-Leads

# Ship a change
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
| Everything offline after a reboot | Nobody has signed in to Windows, so Docker Desktop never started — step 6 |
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
