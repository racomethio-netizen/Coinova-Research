#!/usr/bin/env bash
# One-time hardening + Docker install for a fresh Ubuntu 24.04 LTS VPS. Run as root:
#   sudo bash scripts/provision-ubuntu.sh <deploy-user>
# Idempotent. Review before running: it changes SSH and firewall settings.
set -euo pipefail
DEPLOY_USER="${1:?usage: provision-ubuntu.sh <deploy-user>}"
. /etc/os-release
[[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.04" ]] || { echo "Ubuntu 24.04 required"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq ca-certificates curl gnupg ufw fail2ban unattended-upgrades git

# --- Docker Engine from Docker's official repository ---------------------------
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# Log rotation and live-restore for the daemon.
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" },
  "live-restore": true, "no-new-privileges": true }
JSON
systemctl restart docker

# --- Deploy user ------------------------------------------------------------------
id "$DEPLOY_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"
install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" -m 0750 /opt/nexus

# --- Firewall: only SSH, HTTP (ACME + redirect) and HTTPS ---------------------------
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
# Docker publishes ports by editing iptables directly, bypassing ufw. Compose here
# publishes only Nginx (80/443) publicly; everything else binds to 127.0.0.1 or nothing.

# --- SSH: keys only ---------------------------------------------------------------
if [[ -s "/home/$DEPLOY_USER/.ssh/authorized_keys" || -s /root/.ssh/authorized_keys ]]; then
  cat > /etc/ssh/sshd_config.d/90-nexus.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
SSH
  systemctl reload ssh
else
  echo "WARNING: no authorized_keys found; leaving SSH password auth unchanged" >&2
fi

# --- fail2ban for SSH; automatic security updates ----------------------------------
cat > /etc/fail2ban/jail.d/nexus.conf <<'F2B'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
F2B
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

# --- Clock: UTC + NTP (exchange signatures need accurate time) ----------------------
timedatectl set-timezone UTC
timedatectl set-ntp true

echo "Provisioned. Next: clone the repo into /opt/nexus as $DEPLOY_USER and follow docs/DEPLOYMENT.md"
