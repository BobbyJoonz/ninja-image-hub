#!/usr/bin/env bash
# Install Ninja Image Hub on a Linux server as a systemd service.
#
#   sudo bash deploy/install_linux.sh                 # first install into /opt/ninja-image-hub
#   sudo bash deploy/install_linux.sh --upgrade       # refresh source, keep .env and data/
#
# What it does: system packages, Node 22, Google Chrome, a virtual display (Xvfb), a
# Python venv, unique secrets, the systemd unit, then prints the exact next commands.
# It never overwrites .env, data/ or vendor/ unless you pass --upgrade, and even then it
# leaves .env, data/ and vendor/ untouched.
set -euo pipefail

APP_DIR=/opt/ninja-image-hub
APP_USER=ninja
PORT=8081
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE=0
[[ "${1:-}" == "--upgrade" ]] && UPGRADE=1

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install_linux.sh" >&2
  exit 1
fi

echo "== 1/7 system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip ca-certificates curl git xvfb rsync \
  fonts-liberation fonts-noto-color-emoji fontconfig \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libasound2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2

echo "== 2/7 Node.js 22"
NODE_MAJOR="$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || echo 0)"
if [[ -z "${NODE_MAJOR}" || "${NODE_MAJOR}" -lt 22 ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
node --version
npm --version

echo "== 3/7 Google Chrome (the gateway drives a real Chrome build)"
if ! command -v google-chrome >/dev/null 2>&1; then
  CHROME_DEB=/tmp/google-chrome-stable_current_amd64.deb
  if curl -fsSL -o "$CHROME_DEB" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb; then
    apt-get install -y "$CHROME_DEB"
  else
    echo "WARNING: Chrome download failed (blocked network?). Install it manually, then"
    echo "         rerun this script. Without Chrome the workers cannot start."
    echo "         Alternative mirror: https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
  fi
fi
command -v google-chrome >/dev/null 2>&1 && google-chrome --version || true

echo "== 4/7 service user and source"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$APP_USER"
if [[ -d "$APP_DIR" && $UPGRADE -eq 0 ]]; then
  echo "$APP_DIR already exists. Re-run with --upgrade to refresh the source," >&2
  echo "or remove it after backing up .env and data/." >&2
  exit 1
fi
mkdir -p "$APP_DIR"
rsync -a --delete \
  --exclude data --exclude vendor --exclude .venv --exclude __pycache__ \
  --exclude .pytest_cache --exclude .env \
  "$SRC_DIR/" "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "== 5/7 Python environment"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" pytest

echo "== 6/7 unique secrets (only if missing; shown by bootstrap itself)"
if [[ ! -f "$APP_DIR/.env" ]]; then
  sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python scripts/bootstrap.py" || true
  echo "SAVE the admin password printed above. It is shown once."
  chmod 600 "$APP_DIR/.env" || true
else
  echo ".env already exists - left unchanged."
fi

echo "== 7/7 systemd unit"
install -m 0644 "$APP_DIR/deploy/ninja-image-hub.service" /etc/systemd/system/ninja-image-hub.service
systemctl daemon-reload
systemctl enable --now ninja-image-hub
sleep 3
systemctl --no-pager --lines=15 status ninja-image-hub || true

cat <<EOF

Installed. Next steps (run as $APP_USER, not root):

  sudo -u $APP_USER -i
  cd $APP_DIR
  .venv/bin/python scripts/doctor.py                  # actionable checklist
  .venv/bin/python scripts/prepare_gateway.py --yes   # gateway + browser binaries
  sudo systemctl restart ninja-image-hub

Open the panel from your laptop through an SSH tunnel (the panel stays on loopback):

  ssh -N -L 8081:127.0.0.1:$PORT <user>@<server>
  # then browse http://127.0.0.1:8081

Add an account in the panel, Start it, and open Accounts -> Login. The login page streams
the server-side browser (rendered into a virtual display), so you can sign in to ChatGPT
without any desktop on the server. Click "Save Session" afterwards.

Logs:  journalctl -u ninja-image-hub -f     (panel)
       tail -f $APP_DIR/data/accounts/*/worker.log  (each browser worker)
EOF
