#!/usr/bin/env bash
# Self-healing network watchdog (system timer, root).
#
# The cabinet's Intel I219 (e1000e) is a known offender for silent hardware
# unit hangs: the machine keeps running while the NIC stops passing traffic
# until something resets the link. Nobody stands next to an arcade cabinet
# with a keyboard, so recovery has to be automatic and escalate by itself:
#
#   gateway unreachable  -> bounce the link
#   still unreachable    -> restart NetworkManager
#   still unreachable    -> reload the NIC driver module
#
# Each tick performs at most one escalation step, tracked in /run so the
# state resets on reboot. A healthy check resets the ladder.
set -uo pipefail

STATE=/run/arcade-net-watchdog.step
step=$(cat "$STATE" 2>/dev/null || echo 0)

log() { echo "[net-watchdog] $*"; }

gw=$(ip route show default | awk '/default/ {print $3; exit}')
if [ -n "$gw" ] && ping -c2 -W2 "$gw" >/dev/null 2>&1; then
    [ "$step" != 0 ] && log "network healthy again (was at step $step)"
    echo 0 > "$STATE"
    exit 0
fi

# no gateway route at all also counts as down
iface=$(ip route show default | awk '/default/ {print $5; exit}')
if [ -z "$iface" ]; then
    # pick the first wired/wireless device NM knows
    iface=$(nmcli -t -g DEVICE,TYPE device 2>/dev/null |
            awk -F: '$2=="ethernet"||$2=="wifi"{print $1; exit}')
fi

step=$((step + 1))
echo "$step" > "$STATE"
log "gateway ${gw:-<none>} unreachable via ${iface:-<none>} — step $step"

case "$step" in
    1)
        [ -n "$iface" ] && { ip link set "$iface" down; sleep 2
                             ip link set "$iface" up; }
        log "bounced link on ${iface:-<none>}"
        ;;
    2)
        systemctl restart NetworkManager
        log "restarted NetworkManager"
        ;;
    *)
        if [ -n "$iface" ]; then
            drv=$(basename "$(readlink -f /sys/class/net/$iface/device/driver 2>/dev/null)" 2>/dev/null)
            if [ -n "$drv" ] && [ "$drv" != "driver" ]; then
                log "reloading driver $drv"
                modprobe -r "$drv" 2>/dev/null; sleep 2; modprobe "$drv"
                sleep 5; systemctl restart NetworkManager
            fi
        fi
        echo 0 > "$STATE"   # full ladder done; start over next time
        ;;
esac
exit 0
