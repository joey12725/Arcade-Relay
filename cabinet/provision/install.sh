#!/usr/bin/env bash
# Provision a fresh Debian-stable cabinet into the arcade kiosk (spec §5.1).
# Run as root from a checkout:  SERVER_HOST=arcade.example.com ./install.sh
#
# What this sets up: auto-login user + X11/Openbox kiosk, all blanking off
# (the screensaver system owns idle), cabinet code under /opt/arcade,
# systemd USER units (cabd/pegasus/steam, Restart=always) with lingering,
# and udev rules for stable controller identities.
#
# NOT installed here (no Debian packages; see DEPLOY.md): pegasus-fe,
# Steam, Moonlight, Greenlight.
set -euo pipefail

ARCADE_USER=${ARCADE_USER:-arcade}
: "${SERVER_HOST:?set SERVER_HOST=<your-cloudflare-tunnel-hostname>}"
SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)   # the repo's cabinet/ directory

apt-get update
apt-get install -y --no-install-recommends \
    xorg openbox xdotool wmctrl x11-utils x11vnc imagemagick \
    mpv retroarch libretro-core-info \
    pipewire pipewire-pulse wireplumber pulseaudio-utils \
    python3 python3-aiohttp python3-evdev python3-pil python3-yaml \
    udev curl ca-certificates procps

# --- auto-login on tty1 (no display manager, spec §5.1) ---
useradd -m -s /bin/bash "$ARCADE_USER" 2>/dev/null || true
HOME_DIR=$(getent passwd "$ARCADE_USER" | cut -d: -f6)
install -d /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $ARCADE_USER --noclear %I \$TERM
EOF

# --- X starts on login; openbox is the session ---
cat > "$HOME_DIR/.bash_profile" <<'EOF'
[ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ] && exec startx -- -nocursor
EOF
cat > "$HOME_DIR/.xinitrc" <<'EOF'
exec openbox-session
EOF
install -d "$HOME_DIR/.config/openbox"
install -m 0755 "$SRC_DIR/provision/openbox-autostart" \
    "$HOME_DIR/.config/openbox/autostart"

# --- cabinet code ---
install -d /opt/arcade
rm -rf /opt/arcade/cabinet
cp -r "$SRC_DIR" /opt/arcade/cabinet
install -d /roms /saves /var/cache/arcade

# --- systemd user units; linger so they run from boot, not first login ---
install -d "$HOME_DIR/.config/systemd/user/default.target.wants"
for unit in cabd pegasus steam; do
    sed "s|@SERVER_HOST@|$SERVER_HOST|g" \
        "$SRC_DIR/provision/systemd/$unit.service" \
        > "$HOME_DIR/.config/systemd/user/$unit.service"
    ln -sf "../$unit.service" \
        "$HOME_DIR/.config/systemd/user/default.target.wants/$unit.service"
done
loginctl enable-linger "$ARCADE_USER"

# --- unattended code updates from GitHub ---
# Only wired up when this checkout is the cabinet's own repo at
# /opt/arcade/repo (the image build puts it there); provisioning from a
# throwaway clone must not leave a timer pointing at a path that vanishes.
if [ "$SRC_DIR" = /opt/arcade/repo/cabinet ]; then
    install -d /etc/arcade
    # install.sh's own inputs, so the timer can re-provision unattended.
    # The branch to track is sticky: read back from the existing file when it
    # is not being set explicitly, so a re-provision never loses it.
    if [ -z "${ARCADE_UPDATE_BRANCH:-}" ] && [ -r /etc/arcade/provision.env ]; then
        ARCADE_UPDATE_BRANCH=$(sed -n 's/^ARCADE_UPDATE_BRANCH=//p' \
            /etc/arcade/provision.env)
    fi
    cat > /etc/arcade/provision.env <<EOF
SERVER_HOST=$SERVER_HOST
ARCADE_USER=$ARCADE_USER
ARCADE_UPDATE_BRANCH=${ARCADE_UPDATE_BRANCH:-}
EOF
    chmod 0644 /etc/arcade/provision.env
    for unit in arcade-update.service arcade-update.timer; do
        install -m 0644 "$SRC_DIR/provision/systemd-system/$unit" \
            "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload 2>/dev/null || true
    # `systemctl enable` works offline (it only writes symlinks), but fall
    # back to writing the symlink by hand rather than fail provisioning.
    if ! systemctl enable arcade-update.timer 2>/dev/null; then
        install -d /etc/systemd/system/timers.target.wants
        ln -sf ../arcade-update.timer \
            /etc/systemd/system/timers.target.wants/arcade-update.timer
    fi
fi

# --- stable controller identities across reboots (spec §5.5) ---
install -D -m 0644 "$SRC_DIR/provision/99-arcade-controllers.rules" \
    /etc/udev/rules.d/99-arcade-controllers.rules
udevadm control --reload 2>/dev/null || true

# re-provisioning a live machine: pick up updated unit files immediately
# (first install: no user manager yet -> harmless no-op)
runuser -u "$ARCADE_USER" -- \
    env XDG_RUNTIME_DIR="/run/user/$(id -u "$ARCADE_USER")" \
    systemctl --user daemon-reload 2>/dev/null || true

chown -R "$ARCADE_USER:$ARCADE_USER" "$HOME_DIR" /opt/arcade /roms /saves \
    /var/cache/arcade

# --- agentic development toolchain (optional; never fails the arcade) ---
if [ "${SKIP_DEVTOOLS:-0}" != "1" ]; then
    ARCADE_USER="$ARCADE_USER" bash "$SRC_DIR/provision/devtools.sh" || true
fi

echo "cabinet provisioned for server $SERVER_HOST — reboot to enter kiosk"
