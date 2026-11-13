#!/usr/bin/env bash
# M5 acceptance (spec §8, sim scope): stream tiles launch end-to-end from
# cabinet controls and HOME_COMBO exits the stream cleanly; xCloud tile
# best-effort. Real SF6@60fps LAN + real xCloud -> docs/hardware-checklist.md.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== accept-m5: stack up =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== moonlight tile: launch (Pegasus path: cabd-launch stream ...) =="
out=$(cab python3 /cabinet/cabd-launch stream "moonlight:Street Fighter 6")
echo "$out" | grep -q '"ok": true' || { echo "FAIL: moonlight launch: $out"; exit 1; }
cab sh -c 'xdotool search --name "moonlight-fake" >/dev/null' || { echo "FAIL: no stream window"; exit 1; }
t=0; until srv arcadectl --json games running | grep -q "stream/moonlight:Street Fighter 6"; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: server never saw stream"; exit 1; }; sleep 0.5
done

echo "== stream is NOT hang-killed (no UDP iface; watchdog must leave it) =="
sleep 4   # > HANG_UNRESPONSIVE_S + poll
cab sh -c 'xdotool search --name "moonlight-fake" >/dev/null' || { echo "FAIL: watchdog killed the stream"; exit 1; }

echo "== HOME_COMBO exits the stream cleanly, frontend focused <2s =="
t0=$(date +%s%3N)
cab sh -c 'echo "COMBO_HOME" > /tmp/input-events'
deadline=$((t0 + 2000)); ok=""
while [ "$(date +%s%3N)" -lt "$deadline" ]; do
    if ! cab sh -c 'xdotool search --name "moonlight-fake" >/dev/null 2>&1'; then ok=1; break; fi
    sleep 0.1
done
t1=$(date +%s%3N)
[ -n "$ok" ] || { echo "FAIL: HOME_COMBO did not exit stream in 2s"; exit 1; }
cab sh -c 'f=$(xdotool getwindowfocus) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]' || { echo "FAIL: frontend not focused"; exit 1; }
echo "   HOME_COMBO -> stream gone in $((t1 - t0))ms"
t=0; until [ "$(srv arcadectl --json games running | python3 -c 'import sys,json; print(json.load(sys.stdin)["running"] or "")')" = "" ]; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: running never cleared"; exit 1; }; sleep 0.5
done

echo "== greenlight (xCloud) tile: best-effort launch + remote kill =="
out=$(cab python3 /cabinet/cabd-launch stream "greenlight:Game Pass Cloud")
echo "$out" | grep -q '"ok": true' || { echo "FAIL: greenlight launch: $out"; exit 1; }
cab sh -c 'xdotool search --name "greenlight-fake" >/dev/null' || { echo "FAIL: no greenlight window"; exit 1; }
srv arcadectl games kill >/dev/null
sleep 1
cab sh -c 'xdotool search --name "greenlight-fake" >/dev/null 2>&1' && { echo "FAIL: greenlight survived remote kill"; exit 1; } || true

echo "ACCEPT-M5 PASS"
