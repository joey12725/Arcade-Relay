#!/usr/bin/env bash
# M1 acceptance (spec §8): EMU-1 bar — 100 consecutive clean scripted
# launch->play->exit cycles PER SYSTEM (local launch path), zero hangs, zero
# config drift (every 10th cycle deliberately scrambles rendered configs
# first), zero input loss (frontend focus verified every cycle); plus
# kill -9 a running emulator -> frontend focused within 2s (5 reps,
# external clock). UI-4 navigation audit + real-Pegasus theming are
# hardware-scope (docs/hardware-checklist.md).
set -euo pipefail
cd "$(dirname "$0")/.."

CYCLES="${ACCEPT_M1_CYCLES:-100}"
SYSTEMS=(nes snes genesis arcade gba psx)
declare -A EXPECT=(
    [nes]='a = "a"' [snes]='a = "b"' [genesis]='a = "c"'
    [arcade]='a = "x"' [gba]='a = "l"' [psx]='a = "cross"'
)

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== accept-m1: stack up =="
compose up -d --build --wait --wait-timeout 300
compose restart cabinet >/dev/null 2>&1; sleep 2
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

total=0
for sys in "${SYSTEMS[@]}"; do
    echo "== ${CYCLES} cycles: $sys (expect ${EXPECT[$sys]}) =="
    for ((i = 1; i <= CYCLES; i++)); do
        scr=0; [ $((i % 10)) -eq 0 ] && scr=1
        if ! out=$(cab sh /simscripts/m1-cycle.sh "$sys" "${EXPECT[$sys]}" "$scr" 2>&1); then
            echo "FAIL: $sys cycle $i: $out"; exit 1
        fi
        total=$((total + 1))
    done
    echo "   $sys: ${CYCLES}/${CYCLES} clean"
done

echo "== kill -9 recovery: 5 reps, frontend focused <2s (external clock) =="
for rep in 1 2 3 4 5; do
    cab python3 /cabinet/cabd-launch snes /roms/snes/game.rom >/dev/null
    sleep 0.3
    t0=$(date +%s%3N)
    cab pkill -9 -f "/fakes/retroarch" || true
    deadline=$((t0 + 2000)); ok=""
    while [ "$(date +%s%3N)" -lt "$deadline" ]; do
        if cab sh -c 'f=$(xdotool getwindowfocus 2>/dev/null) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]'; then
            ok=1; break
        fi
        sleep 0.1
    done
    [ -n "$ok" ] || { echo "FAIL: rep $rep frontend not focused within 2s"; exit 1; }
    t1=$(date +%s%3N)
    echo "   rep $rep: $((t1 - t0))ms"
    # local state must clear too
    t=0; until cab python3 /cabinet/cabd-launch running | grep -q '"running": null'; do
        t=$((t+1)); [ $t -ge 12 ] && { echo "FAIL: rep $rep running never cleared"; exit 1; }; sleep 0.25
    done
done

echo "ACCEPT-M1 PASS ($total cycles across ${#SYSTEMS[@]} systems, 5/5 kill-9 recoveries <2s)"
