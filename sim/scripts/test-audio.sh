#!/usr/bin/env bash
# AUD-1/AUD-2 on REAL PipeWire: per-class policy enforced on real audio
# nodes (null sink, mpv sine sources), master volume, live CLI changes.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

# node_state CLASS -> prints "muted volume%" for the sink-input with that media.role
node_state() {
    cab python3 -c "
import json, subprocess, sys
r = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
for si in json.loads(r.stdout or '[]'):
    if (si.get('properties') or {}).get('media.role','system' ) == '$1' or ('$1'=='system' and 'media.role' not in (si.get('properties') or {})):
        vol = list(si['volume'].values())[0]['value_percent']
        print(str(si['mute']).lower(), vol); sys.exit(0)
print('absent')"
}

wait_node() { # wait_node CLASS EXPECT_SUBSTR TIMEOUT_S
    local cls="$1" want="$2" timeout="$3" t=0 got=""
    while true; do
        got=$(node_state "$cls")
        case "$got" in *"$want"*) return 0;; esac
        t=$((t + 1)); [ "$t" -ge "$((timeout * 5))" ] && { echo "FAIL: $cls node state '$got' != '$want'"; return 1; }
        sleep 0.2
    done
}

echo "== 0. stack up =="
compose up -d --build --wait --wait-timeout 300
compose restart cabinet >/dev/null 2>&1; sleep 2
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. real sink present; master volume applied from state (70%) =="
t=0; until cab pactl get-default-sink 2>/dev/null | grep -q .; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no default sink"; exit 1; }; sleep 0.5
done
srv arcadectl state push >/dev/null    # ensure policy applied post-restart
t=0; until cab pactl get-sink-volume @DEFAULT_SINK@ | grep -q "70%"; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: master volume not 70% ($(cab pactl get-sink-volume @DEFAULT_SINK@))"; exit 1; }; sleep 0.5
done

echo "== 2. game launch -> real audio node, class games: unmuted, 100% =="
cab python3 /cabinet/cabd-launch snes /roms/snes/game.rom >/dev/null
wait_node games "false" 5
wait_node games "100%" 5

echo "== 3. trailer node appears -> muted by policy (AUD-2: never audible) =="
cab sh -c "env 'PULSE_PROP_media.role=trailers' mpv --no-config --vo=null --ao=pulse --loop=inf --really-quiet av://lavfi:sine=frequency=880 >/dev/null 2>&1 &"
wait_node trailers "true" 3

echo "== 4. live CLI change: mute games -> node muted; unmute -> restored =="
srv arcadectl volume set --class games --mute >/dev/null
wait_node games "true" 3
srv arcadectl volume set --class games --unmute >/dev/null
wait_node games "false" 3
srv arcadectl state log | grep -q "volume set games.muted" || { echo "FAIL: no git commit for volume change"; exit 1; }

echo "== 5. master change via CLI =="
srv arcadectl volume set --master 55 >/dev/null
t=0; until cab pactl get-sink-volume @DEFAULT_SINK@ | grep -q "55%"; do
    t=$((t+1)); [ $t -ge 15 ] && { echo "FAIL: master not 55%"; exit 1; }; sleep 0.2
done
srv arcadectl volume set --master 70 >/dev/null   # restore default

echo "== 6. cleanup =="
cab pkill -f "sine=frequency=880" || true
cab python3 /cabinet/cabd-launch kill >/dev/null

echo "TEST-AUDIO PASS"
