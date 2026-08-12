#!/usr/bin/env bash
#
# One-time bootstrap for a fresh Ubuntu 24.04 VPS that will run LeadLens.
#
# Run once, as root. Two ways in, because providers differ on whether root may log in:
#
#     ssh root@YOUR_SERVER 'bash -s' < deploy/server-setup.sh              # Hetzner, DO, Vultr
#     ssh ubuntu@YOUR_SERVER 'sudo bash -s' < deploy/server-setup.sh       # Oracle Cloud, AWS
#
# For the Tailscale topology, which needs the tailscale package installed, pass TOPOLOGY:
#
#     ssh ubuntu@YOUR_SERVER 'sudo TOPOLOGY=tailscale bash -s' < deploy/server-setup.sh
#
# Idempotent: safe to re-run. Every step checks its own state first.
#
# The firewall stance is the point of this script. LeadLens is reached through a Cloudflare
# tunnel or a Tailscale tailnet, both of which dial OUT from this machine. Nothing needs to
# listen on 80 or 443, so nothing is allowed to. The only inbound port is SSH.

set -euo pipefail

APP_USER="leadlens"
APP_DIR="/opt/leadlens"
SWAP_SIZE="2G"
TOPOLOGY="${TOPOLOGY:-cloudflare}"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
ok()  { printf '    \033[0;32mok\033[0m %s\n' "$*"; }

if [[ $EUID -ne 0 ]]; then
  echo "Run this as root. Either:" >&2
  echo "  ssh root@server 'bash -s' < deploy/server-setup.sh" >&2
  echo "  ssh ubuntu@server 'sudo bash -s' < deploy/server-setup.sh   # if root login is disabled" >&2
  exit 1
fi

case "$TOPOLOGY" in
  cloudflare|tailscale) ;;
  *) echo "TOPOLOGY must be 'cloudflare' or 'tailscale', got '$TOPOLOGY'" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------------------
# Find the SSH key that is already working, so the deploy user can inherit it.
#
# This has to be discovered rather than assumed. On Hetzner/DO the key is in root's
# authorized_keys. On Oracle Cloud and AWS, root login is disabled and the key lives with the
# admin user ($SUDO_USER when this script is invoked through sudo) -- and, worse, Oracle *does*
# ship a /root/.ssh/authorized_keys, but every entry is prefixed with a forced
# command= that only prints "please login as the user ubuntu". Copying that to the deploy user
# produces an account that authenticates and then refuses to do anything, which looks exactly
# like a broken deploy script. So: prefer the sudo-invoking user, and ignore forced-command
# entries wherever they appear.
ADMIN_KEYS=""
ADMIN_KEY_LINES=""
_candidates=()
[[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]] \
  && _candidates+=("/home/${SUDO_USER}/.ssh/authorized_keys")
_candidates+=("/root/.ssh/authorized_keys")

for _candidate in "${_candidates[@]}"; do
  [[ -s "$_candidate" ]] || continue
  _usable="$(grep -vE '^[[:space:]]*(#|$)' "$_candidate" | grep -v 'command=' || true)"
  if [[ -n "$_usable" ]]; then
    ADMIN_KEYS="$_candidate"
    ADMIN_KEY_LINES="$_usable"
    break
  fi
done

# ---------------------------------------------------------------------------------------
log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq ca-certificates curl gnupg ufw unattended-upgrades ca-certificates
ok "base packages installed"

# ---------------------------------------------------------------------------------------
log "Docker Engine"
if command -v docker >/dev/null 2>&1; then
  ok "docker already present ($(docker --version))"
else
  # Docker's own apt repo, not the Ubuntu/snap builds: those lag badly and the snap one
  # sandboxes the daemon in ways that break bind mounts.
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  ok "docker installed ($(docker --version))"
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------------------
log "Application user and directory"
if id -u "$APP_USER" >/dev/null 2>&1; then
  ok "user $APP_USER exists"
else
  adduser --disabled-password --gecos "" "$APP_USER"
  ok "created user $APP_USER"
fi
usermod -aG docker "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_DIR"
ok "$APP_DIR ready"

# Let the deploy user in over SSH with the same key that got us in. deploy.sh connects as
# $APP_USER, so this step is what makes deployment possible at all -- if it is skipped, the
# next thing you see is "Permission denied (publickey)" from deploy.sh.
if [[ -n "$ADMIN_KEY_LINES" ]]; then
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "/home/$APP_USER/.ssh"
  printf '%s\n' "$ADMIN_KEY_LINES" > "/home/$APP_USER/.ssh/authorized_keys"
  chown "$APP_USER:$APP_USER" "/home/$APP_USER/.ssh/authorized_keys"
  chmod 0600 "/home/$APP_USER/.ssh/authorized_keys"
  ok "authorized_keys installed for $APP_USER (from $ADMIN_KEYS)"
else
  echo "    WARNING: found no usable SSH key in ${_candidates[*]}."
  echo "    $APP_USER has no key, so deploy/deploy.sh will not be able to connect."
  echo "    Install your key for the admin user (ssh-copy-id), then re-run this script."
fi

# ---------------------------------------------------------------------------------------
log "Firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
# SSH only. Deliberately NOT 80/443: the Cloudflare tunnel is an outbound connection, so
# opening web ports here would only create a way to bypass the Access policy.
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
ok "inbound: SSH only (no 80/443 by design)"

# Oracle Cloud's Ubuntu images ship their own iptables rules via netfilter-persistent, which
# sit alongside ufw's chains rather than being replaced by them. They only ever deny more than
# ufw does, and this deployment needs no inbound port beyond SSH, so leaving them alone is
# both safe and deliberate -- rewriting another vendor's firewall over SSH is how people lock
# themselves out. Flagged rather than "fixed" so the state is not a surprise later.
if [[ -f /etc/iptables/rules.v4 ]] && grep -qiE 'REJECT|DROP' /etc/iptables/rules.v4 2>/dev/null; then
  echo "    note: provider iptables rules also present (/etc/iptables/rules.v4), left intact."
fi

# ---------------------------------------------------------------------------------------
log "Swap"
if swapon --show | grep -q '/swapfile'; then
  ok "swapfile already active"
else
  fallocate -l "$SWAP_SIZE" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Insurance against an OOM during model retraining taking down sshd with it.
  ok "${SWAP_SIZE} swapfile active"
fi

# ---------------------------------------------------------------------------------------
log "Unattended security upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
ok "security patches apply automatically"

# ---------------------------------------------------------------------------------------
if [[ "$TOPOLOGY" == "tailscale" ]]; then
  log "Tailscale"
  if command -v tailscale >/dev/null 2>&1; then
    ok "tailscale already present ($(tailscale version | head -n1))"
  else
    # Official install script; the package repo it configures is what keeps tailscaled patched
    # via unattended-upgrades along with everything else.
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "tailscale installed ($(tailscale version | head -n1))"
  fi
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
  # Deliberately not running `tailscale up` here: it prints an authentication URL that has to
  # be opened in a browser, which cannot work inside `ssh 'bash -s'` with no TTY. See
  # deploy/RUNBOOK-TAILSCALE.md step 3.
  ok "tailscaled running (not yet logged in -- see RUNBOOK-TAILSCALE.md)"
fi

# ---------------------------------------------------------------------------------------
log "SSH hardening"
# Guarded on purpose. Turning off password authentication when no key is installed locks you
# out of the server permanently, and there is no way back in without console access from the
# provider. So this only runs when a key is demonstrably already working -- and the key it
# checks for is the discovered one, not root's, since on Oracle/AWS root has none.
if [[ -n "$ADMIN_KEY_LINES" ]]; then
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  # Ubuntu 24.04 ships drop-in configs that can re-enable passwords after the main file.
  for f in /etc/ssh/sshd_config.d/*.conf; do
    [[ -e "$f" ]] && sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' "$f"
  done
  if sshd -t; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    ok "password login disabled, key-only access"
  else
    echo "    sshd config test FAILED -- leaving SSH settings untouched" >&2
  fi
else
  echo "    SKIPPED: no usable SSH key found for root or ${SUDO_USER:-the admin user}."
  echo "    Password login left ENABLED so you are not locked out."
  echo "    Install your key (ssh-copy-id), then re-run this script."
fi

# ---------------------------------------------------------------------------------------
log "Done"
if [[ "$TOPOLOGY" == "tailscale" ]]; then
cat <<EOF

  Server is ready (Tailscale topology). Next, per deploy/RUNBOOK-TAILSCALE.md:

    1. On this server:  sudo tailscale up
       Then:            sudo tailscale serve --bg 8000

    2. Create ${APP_DIR}/.env on this server (RUNBOOK-TAILSCALE.md step 4).
       It must set LEADLENS_TAILSCALE_AUTH=1, or every tailnet user gets delete rights.

    3. From your machine:  TOPOLOGY=tailscale ./deploy/deploy.sh ${APP_USER}@<this-server-ip>

EOF
else
cat <<EOF

  Server is ready (Cloudflare topology). Next:

    1. Create ${APP_DIR}/.env on this server (see deploy/RUNBOOK.md step 5).
       It holds the tunnel token and must never be committed or copied from your laptop.

    2. From your machine:  ./deploy/deploy.sh ${APP_USER}@<this-server-ip>

EOF
fi
cat <<EOF
  Firewall state:
$(ufw status verbose | sed 's/^/    /')
EOF
