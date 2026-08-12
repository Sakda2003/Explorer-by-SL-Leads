# Deploying LeadLens for free, always on, for up to 3 people

End state: `https://leadlens.<your-tailnet>.ts.net` works for exactly the people you invite,
runs 24/7 without your laptop, and costs **$0/month**. The machine has no inbound port open at
all except SSH.

Nothing here is cloud-specific. Any machine running **Ubuntu 24.04** works — a mini PC on a
shelf in the office, a spare desktop, a Raspberry Pi, or a VPS — because Tailscale dials
outbound and needs no public IP, no port forwarding, and no domain. Step 1 covers the choice.

This is the alternative to [RUNBOOK.md](RUNBOOK.md), which uses Cloudflare Access and needs a
domain (~$12/yr). Pick one; do not run both.

| | Cloudflare (RUNBOOK.md) | Tailscale (this file) |
|---|---|---|
| Cost | ~$12/yr domain + VPS | $0 |
| Domain needed | Yes | No |
| Users | 50 (free tier) | **3 (free tier)** |
| Colleagues install anything | No, any browser | Yes, Tailscale client |
| Identity | Signed JWT, verified | Proxy header, trusted by topology |

Budget roughly 60 minutes once the machine has Ubuntu on it, plus however long the OS install
takes if you are starting from bare hardware. Steps 1, 3, 4 and 6 are yours (hardware, the
Tailscale sign-in, the allowlist, the invites); steps 2, 5 and 7 are scripted.

> **The 3-user ceiling is the free plan's, not this app's.** If a fourth person ever needs
> access, that is Tailscale's paid tier, and switching to the Cloudflare topology (50 users
> free, but a domain) may become the cheaper answer. Worth knowing before you build habits
> around this.

---

## 1. Provide the machine  *(you)*

Three viable routes. **A is recommended** — it needs no card, no cloud account, and keeps
customer data in your own office.

### A. A machine at the office  *(recommended, $0/mo, no card)*

Any always-on x86 or ARM box. In order of preference:

1. **A spare desktop or old laptop you already own.** Free. For a laptop, set it to ignore
   lid-close so it keeps running shut:
   ```bash
   sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
   sudo systemctl restart systemd-logind
   ```
2. **A used mini PC** — ThinkCentre Tiny, Dell OptiPlex Micro and similar run ~$80–150 second
   hand. 4 GB RAM is comfortable; the container is capped at 2 GB.
3. **A Raspberry Pi 4/5** (4 GB+). Works fine — the `Dockerfile` builds on `arm64`. But **boot
   it from an SSD or a decent USB drive, never an SD card.** SD cards fail under sustained
   database writes, and this machine holds the only live copy of your leads.

Install **Ubuntu Server 24.04**, and during setup tick "Install OpenSSH server" and add your
SSH public key. Note the machine's LAN IP (`ip -4 addr`) — you only need it for step 2; after
Tailscale is up you can reach it by name.

Two settings so it survives a power cut unattended:

- In the BIOS/UEFI, enable **"Restore on AC Power Loss"** (sometimes "AC Back", "After Power
  Failure → Power On"). Without it the machine stays off until someone presses the button.
- Nothing needed for the app itself: `restart: unless-stopped` in `docker-compose.yml` already
  brings the container back on boot.

You do **not** need to touch your office router — no port forwarding, no static IP, and CGNAT
is irrelevant. Tailscale connects outbound.

> **This is the privacy-preferable option.** ~2,700 customer names stay on hardware you
> physically control, in the country they were collected in.

### B. Oracle Cloud Always Free  *(free, but needs a working card and has real caveats)*

- Sign up at <https://cloud.oracle.com>; a card is required for identity verification, though
  Always Free resources are not charged. Pick a **home region** near you — Singapore is closest
  to Phnom Penh.
- Instance: shape `VM.Standard.A1.Flex` (Ampere ARM, 4 OCPU / 24 GB free), image Ubuntu 24.04,
  and **add your SSH public key** during creation.
- No port needs opening in Oracle's Security List / VCN.

Expect two problems rather than debugging them: **"out of host capacity" on the ARM shape is
common** (retry over a day or two, or try another region), and **Oracle reclaims Always Free
instances left idle** under roughly 20% utilisation for 7 days — an app serving 3 people sits
well below that, so the failure mode is your dashboard quietly disappearing.

### C. A cheap VPS  *(~€4/mo, no free-tier caveats)*

Hetzner CX22, or DigitalOcean / Vultr. Follow [RUNBOOK.md](RUNBOOK.md) step 1 for sizing. If a
local card is rejected at signup, all three accept **PayPal** — a card failing 3-D Secure on an
international signup is a different problem from lacking funds, and PayPal often goes through.

> For B and C: this puts customer names on a server in another country. Choose the region
> deliberately rather than accepting a default.

## 2. Bootstrap the machine  *(scripted)*

Below, `ADMIN` is the user you log in as and `MACHINE` is its IP:

- **Office machine:** the account you created during the Ubuntu install, LAN IP.
- **Oracle:** `ubuntu`, public IP.
- **Hetzner/DO/Vultr:** usually `root`, public IP.

`TOPOLOGY=tailscale` is what makes the script install Tailscale rather than assuming a
Cloudflare tunnel:

```bash
ssh ADMIN@MACHINE 'sudo TOPOLOGY=tailscale bash -s' < deploy/server-setup.sh
```

If you are logging in as `root` (Hetzner/DO/Vultr), drop the `sudo`. Everywhere else keep it —
Oracle and a normal Ubuntu install both disable direct root login.

Installs Docker and Tailscale, creates the `leadlens` deploy user, enables a firewall allowing
only SSH, adds 2 GB swap, and turns on automatic security updates.

Watch its output for the `authorized_keys installed for leadlens` line. If it instead warns
that it found no usable key, `deploy.sh` in step 5 will not be able to connect — run
`ssh-copy-id ADMIN@MACHINE` and re-run this script.

> Why the script hunts for that key rather than reading root's: on Oracle, `/root/.ssh/
> authorized_keys` **does** exist, but every entry is a forced command that only prints "please
> login as the user ubuntu". Copying it to the deploy user yields an account that authenticates
> and then refuses to do anything. The script prefers the sudo-invoking user's keys and skips
> forced-command entries for exactly this reason.

## 3. Join the tailnet and publish the app  *(you, on the machine)*

```bash
ssh ADMIN@MACHINE
sudo tailscale up
```

It prints a URL. Open it in your browser and sign in — that creates your tailnet and adds this
machine to it. Then, in the Tailscale admin console (<https://login.tailscale.com/admin>):

1. **DNS → MagicDNS:** enable it.
2. **DNS → HTTPS Certificates:** enable it. `tailscale serve` cannot issue the HTTPS
   certificate without this, and will fail in the next command.

Back on the machine, give it a readable name and publish port 8000:

```bash
sudo tailscale set --hostname=leadlens
sudo tailscale serve --bg 8000
sudo tailscale serve status
```

`serve status` prints the URL your colleagues will use, e.g.
`https://leadlens.tailXXXX.ts.net`. Note it down.

What this does: Serve terminates HTTPS on the tailnet and proxies to `127.0.0.1:8000`, which
is exactly what `docker-compose.tailscale.yml` binds. It also stamps each request with the
caller's tailnet identity in a `Tailscale-User-Login` header — that header is what step 4
turns into the read/write split.

> **Never use `tailscale funnel` here.** Funnel is the sibling command that exposes a service
> to the *public internet*, and funnel traffic carries no identity. `backend/auth.py` refuses
> requests with no identity header, so the app would simply 401 for everyone — but do not go
> looking for a way around that. It exists to stop a PII database being published by a typo.

## 4. Create the `.env`  *(you, on the machine)*

```bash
sudo -u leadlens nano /opt/leadlens/.env
```

```dotenv
LEADLENS_TAILSCALE_AUTH=1
LEADLENS_ALLOWED_EMAILS=you@yourcompany.com,colleague@yourcompany.com
LEADLENS_WRITER_EMAILS=you@yourcompany.com
LEADLENS_CORS_ORIGINS=
```

Then:

```bash
sudo chmod 600 /opt/leadlens/.env
sudo chown leadlens:leadlens /opt/leadlens/.env
```

No `CF_*` values and no tunnel token — those belong to the other topology.

Three things worth understanding:

- **`LEADLENS_TAILSCALE_AUTH=1` is not optional.** Without it `backend/auth.py` goes inert and
  every tailnet member can delete leads and trigger retrains, with the dashboard looking
  identical either way. `deploy.sh` refuses to deploy if it is missing, precisely because this
  is invisible from the outside.
- **The emails must match the identity Tailscale reports**, which is the account each person
  signs into Tailscale with (their Google address if the tailnet uses Google SSO). Not their
  work alias, if those differ. `tailscale serve status` and the app log show what actually
  arrives if a login is being rejected.
- **`LEADLENS_WRITER_EMAILS` listing only you** gives the others the dashboard without the
  ability to delete a lead or kick off a retrain by accident. Leave it blank to give everyone
  full access.

## 5. Deploy  *(scripted)*

From this repo on your laptop:

```bash
TOPOLOGY=tailscale ./deploy/deploy.sh leadlens@MACHINE
```

`TOPOLOGY=tailscale` matters: it applies `docker-compose.tailscale.yml` (the loopback binding)
and starts the `app` service **only**. Without it, Docker also starts `cloudflared`, which has
no token in this topology and restart-loops forever, burying the app's own logs.

Re-run this any time you want to ship changes. The first build on ARM takes a while (the
frontend build plus Python wheels); later ones reuse cached layers.

Once it is up, you can reach it over the tailnet from your laptop too — install Tailscale
locally and open the URL from step 3.

## 6. Invite your colleagues  *(you)*

In the Tailscale admin console: **Users → Invite users**, and send each person an invite.
They install the Tailscale client (Windows/Mac/iOS/Android), sign in, and then the URL from
step 3 works for them from anywhere.

Remember: **3 users total on the free plan, including you.**

## 7. Verify  *(do all five)*

1. From a machine on the tailnet, open the URL → dashboard loads.
2. From a machine **not** on the tailnet, open it → does not resolve or connect at all.
3. If you set `LEADLENS_WRITER_EMAILS` to only yourself, have a colleague confirm the dashboard
   loads, and that deleting a lead returns "This account has read-only access."
4. Confirm the app is not reachable directly:

   ```bash
   curl -m 5 http://MACHINE:8000/     # must fail to connect
   ```

   If that answers, stop: check `sudo ufw status`, and that the deploy used
   `TOPOLOGY=tailscale` (without it, `app` has no loopback binding).
5. Confirm the gate is actually on:

   ```bash
   ssh leadlens@MACHINE 'cd /opt/leadlens && docker compose -f docker-compose.yml -f docker-compose.tailscale.yml logs app | grep -i tailscale'
   ```

   You want `Tailscale identity enforced`. If you see "No access gate is configured", the app
   is open to every tailnet member — fix `.env` and redeploy.

---

## Everyday operations

`docker compose` needs both files every time in this topology, so define a shortcut on the
server (`~/.bashrc`) to avoid getting it wrong:

```bash
alias dc='docker compose -f /opt/leadlens/docker-compose.yml -f /opt/leadlens/docker-compose.tailscale.yml'
```

```bash
# Ship a change (from your laptop)
TOPOLOGY=tailscale ./deploy/deploy.sh leadlens@MACHINE

# Logs / restart (on the machine)
cd /opt/leadlens && dc logs -f app
cd /opt/leadlens && dc restart app

# Revoke someone
#   Tailscale admin console -> Users -> remove the user
#   Then also remove them from LEADLENS_ALLOWED_EMAILS in .env and restart.
```

Removing them from the tailnet is the gate that matters; the `.env` edit is the second gate.
Do both.

### Moving your existing database across

The new machine starts with an empty database. To carry over the current one:

```bash
# On your laptop -- .backup is safe on a live WAL database; a file copy is not.
python -c "import sqlite3; s=sqlite3.connect('data/leadlens.db'); d=sqlite3.connect('leadlens-transfer.db'); s.backup(d); d.close(); s.close()"

scp leadlens-transfer.db leadlens@MACHINE:/tmp/
ssh leadlens@MACHINE '
  cd /opt/leadlens
  dc stop app
  dc run --rm -v /tmp:/xfer app cp /xfer/leadlens-transfer.db /data/leadlens.db
  dc start app
  rm /tmp/leadlens-transfer.db
'
```

At ~147 MB this upload takes a few minutes.

### Backups — Google Drive variant  *(no card required)*

Follow **step 8 of [RUNBOOK.md](RUNBOOK.md)** for the passphrase, the schedule, and the restore
drill — the encryption and restore paths are identical in both topologies. Two adjustments:

**1. Off-site storage: use Google Drive.** Backblaze B2 and Cloudflare R2 both want a card at
signup. Google Drive gives 15 GB free on an account you already have, and `deploy/backup.sh`
just hands `$RCLONE_REMOTE` to rclone, so no code changes:

```bash
sudo apt install -y rclone
rclone config
```

In the prompts: `n` for a new remote, name it `gdrive`, storage type `drive`, leave client
id/secret blank, scope `1` (full access) or `3` (its own folder only — sufficient and tighter).
It will ask to authorise in a browser; on a headless office machine choose **`n`** at "Use web
browser to automatically authenticate?" and it prints a command to run on your laptop instead,
which gives you a token to paste back.

```bash
rclone mkdir gdrive:leadlens-backups
rclone lsd gdrive:                      # confirm it works
```

Then in `/opt/leadlens/backup.env`:

```dotenv
LEADLENS_BACKUP_PASSPHRASE=<from RUNBOOK.md step 8a>
RCLONE_REMOTE=gdrive:leadlens-backups
KEEP_LOCAL=7
KEEP_REMOTE_DAYS=30
```

Backups are AES-256-GCM encrypted *before* they leave the machine, so Google never holds
readable customer data — the passphrase is what protects them, which is why it must live in
your password manager and not only on this machine.

**2. The restore commands** in RUNBOOK.md step 8f use plain `docker compose`. Add
`-f docker-compose.yml -f docker-compose.tailscale.yml`, or use the `dc` alias above.

> **On an office machine this is the whole safety net, and it is more important than on a VPS,
> not less.** A machine on a shelf can be stolen, flooded, or have its disk die, and there is no
> provider snapshot to fall back on. Do the restore drill (step 8f) once now, while nothing is
> wrong.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `tailscale serve` fails on certificates | HTTPS Certificates not enabled in the admin console (step 3) |
| URL does not resolve for a colleague | MagicDNS off, or they are not signed into the tailnet |
| Everyone gets 401 | `.env` has `LEADLENS_TAILSCALE_AUTH=1` but Serve is not in front — check `tailscale serve status`. Also the symptom if funnel is used instead of serve. |
| Everyone gets 403 | Email in `LEADLENS_ALLOWED_EMAILS` does not match their Tailscale login; check the app log for the rejected address |
| Colleague can delete leads | `LEADLENS_WRITER_EMAILS` blank, or `LEADLENS_TAILSCALE_AUTH` unset (check startup log) |
| `cloudflared` restart-looping | Deployed without `TOPOLOGY=tailscale` |
| Retrain dies, exit code 137 | Container hit its 2 GB limit; raise it in `docker-compose.yml` |
| Instance vanished (cloud) | Oracle idle reclamation. This is why the off-site backup is not optional. |
| Locked out of SSH | Cloud: the provider's browser console. Office machine: plug in a keyboard. |
| **Office machine:** unreachable after a power cut | It did not power back on — enable "Restore on AC Power Loss" in the BIOS (step 1A) |
| **Office machine:** unreachable but powered on | Office internet is down, or the laptop suspended on lid-close (step 1A). `tailscale status` from another tailnet device shows whether the node is online. |
| **Office machine:** slow for colleagues off-site | Expected — traffic goes out over your office upload link. Tailscale will use a direct connection where it can and a relay otherwise. |
| `rclone` auth fails on a headless machine | Answer `n` to the browser prompt and use the `rclone authorize` command it prints, from your laptop |
