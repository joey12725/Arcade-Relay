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
    libretro-nestopia libretro-snes9x libretro-genesisplusgx \
    libretro-mgba libretro-gambatte \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libgstreamer-gl1.0-0 \
    gstreamer1.0-plugins-good gstreamer1.0-libav \
    firmware-mediatek firmware-realtek firmware-atheros firmware-ralink \
    dosfstools parted rsync pciutils usbutils rfkill uuid-runtime unzip \
    ethtool \
    pipewire pipewire-pulse wireplumber pulseaudio-utils \
    python3 python3-aiohttp python3-evdev python3-pil python3-yaml python3-paho-mqtt \
    udev curl ca-certificates procps systemd-timesyncd

# --- clock: T3 Connect rejects DPoP proofs dated >5 s ahead of this clock,
# and a minimal Debian ships with no NTP client. 12 s of drift made every
# laptop->cabinet T3 connection fail with a bare 401.
systemctl enable --now systemd-timesyncd 2>/dev/null || true

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
install -m 0644 "$SRC_DIR/../server/integrations.py" /opt/arcade/cabinet/integrations.py
install -d /roms /saves /var/cache/arcade /var/cache/arcade-clips

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

# --- network profiles ---
# Written only when absent so live nmcli edits are never stomped by the
# 15-minute re-provision. Priorities: wired > home wifi > phone hotspot;
# autoconnect-retries=0 means NetworkManager retries forever (a cabinet
# must never give up on its network and sit there quietly offline).
write_nm_profile() {  # $1=filename $2=content
    local f="/etc/NetworkManager/system-connections/$1"
    [ -e "$f" ] && return 0
    install -d -m 0755 /etc/NetworkManager/system-connections
    printf '%s\n' "$2" > "$f"
    chmod 0600 "$f"
}
write_nm_profile arcade-wired.nmconnection "[connection]
id=arcade-wired
type=ethernet
autoconnect=true
autoconnect-priority=200
autoconnect-retries=0

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default"
write_nm_profile arcade-wifi.nmconnection "[connection]
id=Panic! At The Cisco
type=wifi
autoconnect=true
autoconnect-priority=100
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=Panic! At The Cisco

[wifi-security]
key-mgmt=wpa-psk
psk=9876543210

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default"
write_nm_profile arcade-hotspot.nmconnection "[connection]
id=Mangdang
type=wifi
autoconnect=true
autoconnect-priority=50
autoconnect-retries=0

[wifi]
mode=infrastructure
ssid=Mangdang

[wifi-security]
key-mgmt=wpa-psk
psk=9876543210

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default"
nmcli connection reload 2>/dev/null || true

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
    chmod +x "$SRC_DIR/provision/arcade-update.sh" \
             "$SRC_DIR/provision/arcade-net-watchdog.sh" 2>/dev/null || true
    for unit in arcade-update.service arcade-update.timer \
                arcade-net-watchdog.service arcade-net-watchdog.timer; do
        install -m 0644 "$SRC_DIR/provision/systemd-system/$unit" \
            "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload 2>/dev/null || true
    # `systemctl enable` works offline (it only writes symlinks), but fall
    # back to writing the symlink by hand rather than fail provisioning.
    for t in arcade-update.timer arcade-net-watchdog.timer; do
        if ! systemctl enable "$t" 2>/dev/null; then
            install -d /etc/systemd/system/timers.target.wants
            ln -sf "../$t" "/etc/systemd/system/timers.target.wants/$t"
        fi
        systemctl start "$t" 2>/dev/null || true
    done
fi

# --- Intel I219 (e1000e) hang mitigation ---
# The OptiPlex NIC is notorious for silent "hardware unit hangs" under
# offload load; turning TSO/GSO off is the long-standing fix. Applied to
# every ethernet device NM brings up, via a dispatcher hook.
install -d /etc/NetworkManager/dispatcher.d
cat > /etc/NetworkManager/dispatcher.d/50-arcade-ethtool <<'EOF'
#!/bin/sh
# e1000e hang mitigation: strip offloads when a wired link comes up
[ "$2" = "up" ] || exit 0
case "$(basename "$(readlink -f "/sys/class/net/$1/device/driver" 2>/dev/null)")" in
    e1000e) ethtool -K "$1" tso off gso off gro off 2>/dev/null || true ;;
esac
exit 0
EOF
chmod 0755 /etc/NetworkManager/dispatcher.d/50-arcade-ethtool

# a panicked kernel must reboot, not sit at a dead screen forever
install -d /etc/sysctl.d
echo "kernel.panic = 10" > /etc/sysctl.d/90-arcade-panic.conf
sysctl -p /etc/sysctl.d/90-arcade-panic.conf 2>/dev/null || true

# --- cores Debian does not package, and RetroArch joypad profiles ---
# Network fetches, so best-effort: a cabinet being re-provisioned offline
# keeps whatever it already has.
LIBRETRO_DIR=/usr/lib/x86_64-linux-gnu/libretro
for c in pcsx_rearmed mame2003_plus fbneo; do
    [ -f "$LIBRETRO_DIR/${c}_libretro.so" ] && continue
    if curl -fsSL -m 300 -o "/tmp/${c}.zip" \
         "https://buildbot.libretro.com/nightly/linux/x86_64/latest/${c}_libretro.so.zip" \
       && unzip -o -q -d /tmp "/tmp/${c}.zip"; then
        install -m 0644 "/tmp/${c}_libretro.so" "$LIBRETRO_DIR/" \
            && echo "core installed: $c"
    else
        echo "core fetch failed (offline?): $c — continuing"
    fi
done
if [ ! -d /usr/share/libretro/autoconfig/udev ]; then
    if git clone -q --depth 1 \
         https://github.com/libretro/retroarch-joypad-autoconfig \
         /tmp/joypad-autoconfig 2>/dev/null; then
        install -d /usr/share/libretro/autoconfig
        cp -r /tmp/joypad-autoconfig/udev /usr/share/libretro/autoconfig/
        echo "joypad autoconfig profiles installed"
    fi
fi

# Cabinet-specific profiles are not present in the upstream database.
install -D -m 0644 "$SRC_DIR/provision/controllers/Brook_P4_Wired_Gamepad.cfg" \
    /usr/share/libretro/autoconfig/udev/Brook_P4_Wired_Gamepad.cfg
for profile in "$SRC_DIR"/provision/controllers/Arcade_*_Panel.cfg; do
    install -m 0644 "$profile" /usr/share/libretro/autoconfig/udev/
done

# --- stable controller identities across reboots (spec §5.5) ---
install -D -m 0644 "$SRC_DIR/provision/99-arcade-controllers.rules" \
    /etc/udev/rules.d/99-arcade-controllers.rules
printf 'uinput\n' > /etc/modules-load.d/arcade-input.conf
modprobe uinput
udevadm control --reload 2>/dev/null || true
udevadm trigger --subsystem-match=misc --sysname-match=uinput

# re-provisioning a live machine: pick up updated unit files immediately
# (first install: no user manager yet -> harmless no-op)
runuser -u "$ARCADE_USER" -- \
    env XDG_RUNTIME_DIR="/run/user/$(id -u "$ARCADE_USER")" \
    systemctl --user daemon-reload 2>/dev/null || true

chown -R "$ARCADE_USER:$ARCADE_USER" "$HOME_DIR" /opt/arcade /roms /saves \
    /var/cache/arcade /var/cache/arcade-clips

# --- agentic development toolchain (optional; never fails the arcade) ---
if [ "${SKIP_DEVTOOLS:-0}" != "1" ]; then
    ARCADE_USER="$ARCADE_USER" bash "$SRC_DIR/provision/devtools.sh" || true
fi

echo "cabinet provisioned for server $SERVER_HOST — reboot to enter kiosk"
