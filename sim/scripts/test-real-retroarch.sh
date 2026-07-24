#!/usr/bin/env bash
# REAL RetroArch (Debian 1.20 + gambatte core) launched by cabd with the
# RENDERED config. UDP VERSION answering proves RetroArch consumed the
# rendered retroarch.cfg (network_cmd_enable lives only there). Covers:
# launch, rendered-config consumption, QUIT via network command, watchdog
# kill -9 recovery, and 20 clean launch/exit cycles on the real emulator.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up (image gains retroarch+gambatte) =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
t=0; until cab test -s /var/cache/arcade-state.json; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no state cache"; exit 1; }; sleep 0.5
done

echo "== 1. generate a minimal valid Game Boy ROM (no downloads) =="
cab python3 -c "
import os
rom = bytearray(32 * 1024)
# entry: nop; jp 0x0150
rom[0x100:0x104] = bytes([0x00, 0xC3, 0x50, 0x01])
logo = bytes.fromhex(
    'CEED6666CC0D000B03730083000C000D0008111F8889000EDCCC6EE6DD'
    'DDD999BBBB67636E0EECCCDDDC999FBBB9333E')
rom[0x104:0x104 + len(logo)] = logo
rom[0x134:0x13E] = b'ARCADELOOP'
rom[0x147] = 0x00; rom[0x148] = 0x00; rom[0x149] = 0x00
chk = 0
for b in rom[0x134:0x14D]:
    chk = (chk - b - 1) & 0xFF
rom[0x14D] = chk
# code at 0x150: jr -2 (spin forever)
rom[0x150:0x152] = bytes([0x18, 0xFE])
os.makedirs('/roms/gb', exist_ok=True)
open('/roms/gb/loop.gb', 'wb').write(bytes(rom))
print('rom written', os.path.getsize('/roms/gb/loop.gb'))"

echo "== 2. cabd launches REAL RetroArch; window appears =="
out=$(cab python3 /cabinet/cabd-launch gb /roms/gb/loop.gb)
echo "$out" | grep -q '"ok": true' || { echo "FAIL: launch: $out"; exit 1; }
cab sh -c 'xdotool search --name RetroArch >/dev/null' || { echo "FAIL: no RetroArch window"; exit 1; }
cab pgrep -x retroarch >/dev/null || { echo "FAIL: no retroarch process"; exit 1; }

echo "== 3. UDP VERSION answered => rendered config consumed =="
V=$(cab python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b'VERSION', ('127.0.0.1', 55355))
print(s.recvfrom(1024)[0].decode().strip())")
echo "   RetroArch reports version: $V"
echo "$V" | grep -qE '^[0-9]+\.[0-9]+' || { echo "FAIL: unexpected VERSION reply '$V'"; exit 1; }
cab grep -q 'network_cmd_enable = "true"' /tmp/rendered/retroarch.cfg || { echo "FAIL: rendered cfg lacks network_cmd"; exit 1; }

echo "== 4. clean kill via cabd (network QUIT path) =="
cab python3 /cabinet/cabd-launch kill >/dev/null
sleep 1
cab pgrep -x retroarch >/dev/null && { echo "FAIL: retroarch survived kill"; exit 1; } || true
cab sh -c 'f=$(xdotool getwindowfocus) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]' || { echo "FAIL: frontend not focused"; exit 1; }

echo "== 5. kill -9 real emulator: watchdog recovers <2s =="
cab python3 /cabinet/cabd-launch gb /roms/gb/loop.gb >/dev/null
t0=$(date +%s%3N)
cab pkill -9 -x retroarch || true
deadline=$((t0 + 2000)); ok=""
while [ "$(date +%s%3N)" -lt "$deadline" ]; do
    if cab sh -c 'f=$(xdotool getwindowfocus 2>/dev/null) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]'; then ok=1; break; fi
    sleep 0.1
done
t1=$(date +%s%3N)
[ -n "$ok" ] || { echo "FAIL: no recovery within 2s of kill -9"; exit 1; }
echo "   kill-9 -> frontend in $((t1 - t0))ms"

echo "== 6. 20 real launch/exit cycles, zero failures =="
for i in $(seq 1 20); do
    out=$(cab python3 /cabinet/cabd-launch gb /roms/gb/loop.gb)
    echo "$out" | grep -q '"ok": true' || { echo "FAIL: cycle $i launch: $out"; exit 1; }
    cab python3 /cabinet/cabd-launch kill >/dev/null
done
cab pgrep -x retroarch >/dev/null && { echo "FAIL: leftover retroarch"; exit 1; } || true
echo "   20/20 clean"

echo "TEST-REAL-RETROARCH PASS"
