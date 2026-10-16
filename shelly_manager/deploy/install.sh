#!/usr/bin/env bash
# Install or upgrade Shelly Manager inside a Debian 12/13 LXC. Run as root from the repo checkout.
# Secrets are generated on the host and never printed, except the admin password once on first install.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/shelly-manager
ETC=/etc/shelly-manager
DATA=/var/lib/shelly-manager
BIND_IP="${BIND_IP:?set BIND_IP to the LAN address of this container}"
TLS_NAME="${TLS_NAME:-$(hostname -f)}"

apt-get update -qq
apt-get install -y -qq git curl openssl ca-certificates openssh-client rsync >/dev/null
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

id shelly >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin shelly
install -d -o root -g shelly -m 0750 "$ETC"
install -d -o shelly -g shelly -m 0700 "$DATA"
[ -f "$DATA/devices.txt" ] || { echo "Copy the device list to $DATA/devices.txt, then re-run."; exit 1; }

rsync -a --delete --exclude .git --exclude .venv --exclude data --exclude research "$SRC/" "$APP/" 2>/dev/null \
  || { rm -rf "$APP"; cp -a "$SRC" "$APP"; rm -rf "$APP/.git" "$APP/data" "$APP/research"; }
(cd "$APP" && UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --frozen --no-dev -q)
chown -R root:root "$APP"

if [ ! -f "$ETC/env" ]; then
  sed "s/^SM_BIND_HOST=.*/SM_BIND_HOST=$BIND_IP/" "$APP/deploy/env.example" > "$ETC/env"
fi
if [ ! -f "$ETC/session.secret" ]; then
  openssl rand -base64 48 > "$ETC/session.secret"
fi
if [ ! -f "$DATA/admin.hash" ]; then
  PW="$(openssl rand -base64 18)"
  PW_SAVE="$PW"
  printf '%s' "$PW" | "$APP/.venv/bin/python" -c 'import sys; from shelly_manager.auth import hash_password; print(hash_password(sys.stdin.read()))' > "$DATA/admin.hash"
  NEW_PW=1
  unset PW
fi
if [ ! -f "$ETC/tls.crt" ]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$TLS_NAME" \
    -addext "subjectAltName=DNS:$TLS_NAME,IP:$BIND_IP" \
    -keyout "$ETC/tls.key" -out "$ETC/tls.crt" 2>/dev/null
fi
chown root:shelly "$ETC"/*
chmod 0640 "$ETC"/*
if [ ! -f "$DATA/deploy_key" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "shelly-manager@$(hostname)" -f "$DATA/deploy_key"
  NEW_KEY=1
fi
chown -R shelly:shelly "$DATA"
chmod 0600 "$DATA/devices.txt" "$DATA/admin.hash" "$DATA/deploy_key"
if [ "${NEW_PW:-0}" = 1 ]; then
  # Root-only, never echoed: read it once, store it in a password manager, delete the file.
  umask 077; printf '%s\n' "$PW_SAVE" > /root/shelly-manager-initial-password; unset PW_SAVE
  echo "Initial admin password written to /root/shelly-manager-initial-password (root only)."
fi

install -m 0644 "$APP/deploy/shelly-manager.service" /etc/systemd/system/shelly-manager.service
systemctl daemon-reload
systemctl enable --now shelly-manager
systemctl restart shelly-manager
sleep 3
systemctl --no-pager --lines=5 status shelly-manager
if [ "${NEW_KEY:-0}" = 1 ]; then
  echo "Add this public key as a deploy key WITH write access on the snapshot repo:"
  cat "$DATA/deploy_key.pub"
fi
