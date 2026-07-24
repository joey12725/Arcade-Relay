#!/usr/bin/env bash
# Provision the arcade SERVER on Debian/Ubuntu (spec §6). Run as root from a
# checkout:  ./install.sh
#
# Sets up: arcaded as a system service (Restart=always), arcadectl on PATH,
# the state repo seeded from this checkout's state/, and a cloudflared
# config template. Jellyfin and the Cloudflare tunnel itself are interactive
# one-time steps — see DEPLOY.md.
set -euo pipefail

SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)    # the repo's server/ directory
REPO_DIR=$(cd "$SRC_DIR/.." && pwd)
STATE_DIR=${STATE_DIR:-/var/lib/arcade/state}

apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-aiohttp python3-yaml git curl ca-certificates

# --- code ---
install -d /opt/arcade
rm -rf /opt/arcade/server
cp -r "$SRC_DIR" /opt/arcade/server
install -m 0755 "$SRC_DIR/arcadectl" /usr/local/bin/arcadectl

# --- state repo: seed once from the checkout; never overwrite an existing one ---
if [ ! -d "$STATE_DIR/.git" ]; then
    install -d "$STATE_DIR"
    cp -r "$REPO_DIR/state/." "$STATE_DIR/"
    git -C "$STATE_DIR" init -q
    git -C "$STATE_DIR" -c user.name=arcaded -c user.email=arcaded@localhost \
        add -A
    git -C "$STATE_DIR" -c user.name=arcaded -c user.email=arcaded@localhost \
        commit -qm "initial state (provisioned)"
fi
install -d /var/lib/arcade/saves-backup /var/lib/arcade/rom-staging

# --- systemd system unit ---
install -m 0644 "$SRC_DIR/provision/arcaded.service" \
    /etc/systemd/system/arcaded.service
systemctl daemon-reload
systemctl enable --now arcaded.service

# --- cloudflared template (tunnel creation is interactive; DEPLOY.md) ---
install -d /etc/cloudflared
if [ ! -e /etc/cloudflared/config.yml ]; then
    install -m 0644 "$SRC_DIR/provision/cloudflared-config.yml.example" \
        /etc/cloudflared/config.yml.example
fi

echo "server provisioned: arcaded on :8000, state repo at $STATE_DIR"
echo "next: Jellyfin + Cloudflare tunnel (DEPLOY.md)"
