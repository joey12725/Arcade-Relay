#!/usr/bin/env bash
# Production combo engine + lifeline mode, end-to-end in sim.
# FIFO "<button> down|up" lines drive the SAME engine evdev drives on
# hardware, so hold-time semantics are tested for real.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

fifo() { cab sh -c "printf '$1\n' > /tmp/input-events"; }
game_up() { cab sh -c 'xdotool search --name retroarch-fake >/dev/null 2>&1'; }

echo "== 0. stack up =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. short hold does NOT fire HOME (release at 1s < 2s) =="
cab python3 /cabinet/cabd-launch snes /roms/snes/game.rom >/dev/null
fifo "start down"; sleep 1; fifo "start up"; sleep 1
game_up || { echo "FAIL: game killed by sub-threshold hold"; exit 1; }

echo "== 2. 2s hold fires HOME: game killed, frontend focused =="
fifo "start down"; sleep 2.4; fifo "start up"
t=0; until ! game_up; do t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: HOME combo did not kill game"; exit 1; }; sleep 0.25; done
cab sh -c 'f=$(xdotool getwindowfocus) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]' || { echo "FAIL: frontend not focused"; exit 1; }

echo "== 3. lifeline combo (start+select 5s): status screen appears =="
fifo "start down"; fifo "select down"
sleep 5.6
fifo "start up"; fifo "select up"
cab sh -c 'for i in $(seq 1 20); do xdotool search --name arcade-lifeline >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' || { echo "FAIL: no lifeline screen"; exit 1; }
t=0; until srv arcadectl --json status | grep -q '"lifeline": true'; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: heartbeat never showed lifeline"; exit 1; }; sleep 0.5
done
cab grep -q "reconnect cap -> 1s" /tmp/cabd.log || { echo "FAIL: no aggressive reconnect log"; exit 1; }

echo "== 4. lifeline screen is a live status render (PNG has content) =="
cab sh -c 'test -s /tmp/lifeline.png && head -c8 /tmp/lifeline.png | od -An -tx1' | grep -q "89 50 4e 47" || { echo "FAIL: lifeline PNG missing"; exit 1; }
cab python3 -c "
from PIL import Image
img = Image.open('/tmp/lifeline.png').convert('RGB')
px = list(img.getdata())
reds  = sum(1 for p in px if p == (226, 59, 59))
blues = sum(1 for p in px if p == (43, 79, 216))
assert reds + blues > 5000, f'no indicator blocks rendered ({reds},{blues})'
print(f'indicators rendered: red px {reds}, blue px {blues}')"

echo "== 5. remote off: arcadectl cab lifeline off -> frontend back =="
srv arcadectl cab lifeline off >/dev/null
sleep 1
cab sh -c 'xdotool search --name arcade-lifeline >/dev/null 2>&1' && { echo "FAIL: lifeline screen survived off"; exit 1; } || true
t=0; until srv arcadectl --json status | grep -q '"lifeline": false'; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: lifeline flag stuck"; exit 1; }; sleep 0.5
done

echo "== 6. screensaver combo (select 2s) force-toggles on =="
SRC_OK=0
if srv arcadectl screensaver sources 2>/dev/null | grep -q '"enabled": true'; then SRC_OK=1; fi
if [ "$SRC_OK" = 0 ]; then
    # no jellyfin in this test run: clips cache may still serve the combo;
    # skip only if neither is possible
    if [ "$(cab sh -c 'ls /var/cache/arcade-clips/*.ts 2>/dev/null | wc -l')" -lt 1 ]; then
        echo "   (no sources and no clips; combo path covered by fallback test)"; echo "TEST-COMBOS PASS"; exit 0
    fi
fi
fifo "select down"; sleep 2.4; fifo "select up"
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' || { echo "FAIL: screensaver combo did not start it"; exit 1; }
fifo "BTN_A down"   # any input exits
sleep 1

echo "TEST-COMBOS PASS"
