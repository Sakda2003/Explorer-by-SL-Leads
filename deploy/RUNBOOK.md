# Deploying LeadLens for two people

End state: `https://leadlens.yourdomain.com` works for exactly two Google accounts, and for
nobody else. The server has no web port open to the internet at all.

> **There are two supported topologies.** This one uses Cloudflare Access: it needs a domain
> (~$12/yr) and a VPS, scales to 50 users, and your colleagues need nothing but a browser.
> The alternative is [RUNBOOK-TAILSCALE.md](RUNBOOK-TAILSCALE.md) — free, no domain, but capped
> at 3 users and everyone must install the Tailscale client. Pick one; do not run both.

Steps 1, 2, 4 and 6 need your accounts and payment details, so they are yours to do. Steps 3,
5 and 7 are scripted.

Budget roughly 45 minutes the first time.

---

## What you need before starting

- A domain name. If you do not have one, buy any cheap `.com` (~$12/yr). It does not need to
  be a nice name; nobody sees it but the two of you.
- A card for the VPS (~$5/mo).
- The CEO's work email address.

---

## 1. Create the server  *(you)*

Any Ubuntu **24.04** box with 2 GB RAM or more works. Cheapest sensible options:

| Provider | Plan | Cost |
|---|---|---|
| Hetzner | CX22 (2 vCPU, 4 GB) | ~€4/mo |
| DigitalOcean | Basic (1 vCPU, 2 GB) | $12/mo |
| Vultr | Regular (1 vCPU, 2 GB) | $10/mo |

Hetzner is the value pick. Pick a region near Phnom Penh if offered (Singapore on Hetzner is
`hil` / Ashburn is US; Hetzner's closest is Singapore). **Add your SSH key during creation** —
that matters for step 3.

> **Data residency.** This puts customer names on a server in another country. If that is a
> concern for Explorer by SL, choose the region deliberately rather than accepting the default.

Note the server's IP address.

## 2. Point your domain at Cloudflare  *(you)*

1. Sign up at <https://dash.cloudflare.com> (free plan is enough).
2. **Add a site** → enter your domain → choose **Free**.
3. Cloudflare shows you two nameservers. Go to wherever you bought the domain and replace its
   nameservers with those two.
4. Wait for Cloudflare to say **Active** (usually minutes, occasionally a few hours).

## 3. Bootstrap the server  *(scripted)*

From this repo on your laptop:

```bash
ssh root@YOUR_SERVER_IP 'bash -s' < deploy/server-setup.sh
```

Installs Docker, creates the `leadlens` user, enables a firewall that allows **only SSH**, adds
swap, and turns on automatic security updates.

It also disables password login — but only if your SSH key is already installed. If it prints
`SKIPPED: no authorized_keys`, run `ssh-copy-id root@YOUR_SERVER_IP` and re-run the script.
This guard exists because disabling passwords without a working key locks you out for good.

## 4. Create the tunnel and the access policy  *(you)*

In the Cloudflare dashboard, open **Zero Trust** (left sidebar). First visit asks you to pick a
team name — anything, e.g. `explorer`. Choose the **Free** plan; it covers 50 users.

**a. Create the tunnel**

1. **Networks → Tunnels → Create a tunnel** → **Cloudflared** → name it `leadlens`.
2. It shows an install command containing a long token. **Copy just the token** (the string
   after `--token`). It is a credential — treat it like a password.
3. On the **Public Hostnames** tab, add:
   - **Subdomain:** `leadlens`  **Domain:** your domain
   - **Type:** `HTTP`  **URL:** `app:8000`

   `app:8000` is the container's name on the private compose network. Not `localhost` — the
   tunnel runs in its own container.

**b. Lock it down**

1. **Access → Applications → Add an application** → **Self-hosted**.
2. Name `LeadLens`, subdomain `leadlens`, your domain.
3. Add a policy: name `Owners`, action **Allow**, include **Emails** → your email and the
   CEO's email. Nothing else.
4. Save, then open the application and copy the **Application Audience (AUD) Tag**.

> Login method: by default Cloudflare emails a one-time PIN, which works with no extra setup.
> If you use Google Workspace, add it under **Settings → Authentication** so the CEO just
> clicks "Sign in with Google".

## 5. Create the server's `.env`  *(you, on the server)*

```bash
ssh leadlens@YOUR_SERVER_IP
nano /opt/leadlens/.env
```

Paste, substituting your values:

```dotenv
CF_ACCESS_TEAM_DOMAIN=yourteam.cloudflareaccess.com
CF_ACCESS_AUD=<the AUD tag from step 4b>
LEADLENS_ALLOWED_EMAILS=you@yourdomain.com,ceo@yourdomain.com
LEADLENS_WRITER_EMAILS=you@yourdomain.com
LEADLENS_CORS_ORIGINS=
CF_TUNNEL_TOKEN=<the tunnel token from step 4a>
```

Then `chmod 600 /opt/leadlens/.env`.

Two things worth understanding:

- **`LEADLENS_ALLOWED_EMAILS` is a second, independent gate.** Cloudflare already restricts who
  gets in; this list is what still protects the data if the tunnel is ever misconfigured.
- **`LEADLENS_WRITER_EMAILS` listing only you** gives the CEO the dashboard without the ability
  to delete a lead or trigger a retrain by accident. Cloudflare's free tier cannot express
  per-route permissions, so this is where that lives. Leave it blank to give both of you full
  access.

## 6. Deploy  *(scripted)*

```bash
./deploy/deploy.sh leadlens@YOUR_SERVER_IP
```

Uploads the source, builds the image on the server, starts both containers, waits for the
healthcheck. Re-run this any time you want to ship changes.

## 7. Verify  *(do all four)*

1. Open `https://leadlens.yourdomain.com` → Cloudflare asks you to sign in → dashboard loads.
2. Open it in a private window with a **different** account → access denied.
3. If you set `LEADLENS_WRITER_EMAILS` to only yourself, have the CEO confirm the dashboard
   loads. Deleting a lead should return "This account has read-only access."
4. Confirm nothing is exposed directly:

   ```bash
   curl -m 5 http://YOUR_SERVER_IP:8000/     # must fail to connect
   curl -m 5 http://YOUR_SERVER_IP/          # must fail to connect
   ```

   Both must time out or refuse. If either answers, stop and check `ufw status` and that
   `docker-compose.yml` has no `ports:` under `app`.

---

## Everyday operations

```bash
# Ship a change
./deploy/deploy.sh leadlens@YOUR_SERVER_IP

# Logs
ssh leadlens@YOUR_SERVER_IP 'cd /opt/leadlens && docker compose logs -f app'

# Restart
ssh leadlens@YOUR_SERVER_IP 'cd /opt/leadlens && docker compose restart'

# Revoke someone
#   Cloudflare: Zero Trust -> Access -> Applications -> LeadLens -> edit policy
#   Then also remove them from LEADLENS_ALLOWED_EMAILS in .env and restart.
```

### Moving your existing database to the server

The server starts with an empty database. To carry over the current one:

```bash
# On your laptop -- .backup is safe on a live WAL database; a file copy is not.
python -c "import sqlite3; s=sqlite3.connect('data/leadlens.db'); d=sqlite3.connect('leadlens-transfer.db'); s.backup(d); d.close(); s.close()"

scp leadlens-transfer.db leadlens@YOUR_SERVER_IP:/tmp/
ssh leadlens@YOUR_SERVER_IP '
  cd /opt/leadlens
  docker compose stop app
  docker compose run --rm -v /tmp:/xfer app cp /xfer/leadlens-transfer.db /data/leadlens.db
  docker compose start app
  rm /tmp/leadlens-transfer.db
'
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Tunnel shows "down" in Cloudflare | Token wrong in `.env`. `docker compose logs cloudflared` |
| 502 through the tunnel | Public hostname URL must be `app:8000`, not `localhost:8000` |
| Everyone gets 403 | `CF_ACCESS_AUD` mismatch, or an email typo in `LEADLENS_ALLOWED_EMAILS` |
| Dashboard loads but every API call 401s | `CF_ACCESS_TEAM_DOMAIN` wrong; must be the bare host, no `https://` |
| Retrain dies, exit code 137 | Container hit its 2 GB memory limit; raise it in `docker-compose.yml` |
| Locked out of SSH | Use the provider's web console; Hetzner and DO both have one |

---

## 8. Backups  *(mostly scripted)*

The database is snapshotted with SQLite's online backup API (never a file copy — in WAL mode
the newest committed rows may still be in `leadlens.db-wal`, so a copy can silently lose data
or be unopenable), gzipped, then encrypted with AES-256-GCM before it leaves the server.

### a. Choose the passphrase  *(you)*

```bash
openssl rand -base64 32
```

> **Store this in your password manager before going further.** It is not recoverable. If the
> VPS dies and the passphrase lived only on the VPS, the backups are permanently unreadable
> and you have achieved nothing. It must exist somewhere the server is not.

### b. Set up off-site storage  *(you)*

Backups that live only on the machine being backed up do not survive that machine. Both of
these have free tiers well above what a 34 MB database needs:

- **Backblaze B2** — 10 GB free, simplest signup.
- **Cloudflare R2** — 10 GB free, no egress fees, same dashboard you already use.

On the server:

```bash
sudo apt install -y rclone
rclone config          # follow prompts; name the remote e.g. "b2"
rclone mkdir b2:leadlens-backups
```

### c. Write the backup config  *(you, on the server)*

```bash
nano /opt/leadlens/backup.env
```

```dotenv
LEADLENS_BACKUP_PASSPHRASE=<the passphrase from step 8a>
RCLONE_REMOTE=b2:leadlens-backups
KEEP_LOCAL=7
KEEP_REMOTE_DAYS=30
```

```bash
chmod 600 /opt/leadlens/backup.env
```

Leaving `RCLONE_REMOTE` blank still works, but the script will warn on every run that a single
disk failure loses the database and every backup together.

### d. Run it once by hand

```bash
/opt/leadlens/deploy/backup.sh
```

It snapshots, then immediately decrypts and integrity-checks what it just wrote, and prints the
row counts it found. If that verification ever fails, the run aborts — a backup job that
reports success for six months and produces an unreadable file on the one day you need it is
worse than none, because you stopped worrying about it.

### e. Schedule it

```bash
crontab -e
```

```cron
15 3 * * * /opt/leadlens/deploy/backup.sh >> /var/log/leadlens-backup.log 2>&1
```

03:15 UTC is ~10:15 in Phnom Penh, outside working hours either way.

### f. Do the restore drill now, not during an incident

Verify any backup without touching live data:

```bash
cd /opt/leadlens
docker compose run --rm --no-deps \
  -e LEADLENS_BACKUP_PASSPHRASE="$(grep PASSPHRASE backup.env | cut -d= -f2-)" \
  -v /var/backups/leadlens:/backups --entrypoint python \
  app /app/deploy/restore.py --check /backups/<filename>
```

A real restore (stops the app; the database it replaces is preserved alongside, so a restore
from the wrong day is reversible):

```bash
docker compose stop app
docker compose run --rm --no-deps \
  -e LEADLENS_BACKUP_PASSPHRASE="..." \
  -v /var/backups/leadlens:/backups --entrypoint python \
  app /app/deploy/restore.py --in-place /data/leadlens.db /backups/<filename>
docker compose start app
```

To pull one back from off-site first: `rclone copy b2:leadlens-backups/<filename> /var/backups/leadlens/`

### What is deliberately not covered

Backups run on a schedule but nothing tells you when they **stop**. For two people this is a
reasonable trade, but it means the failure mode is silent. Either skim
`/var/log/leadlens-backup.log` occasionally, or add a dead-man's-switch ping (healthchecks.io
has a free tier) to the end of `backup.sh`.
