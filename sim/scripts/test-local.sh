#!/usr/bin/env bash
# Prime directive 1: local launches work with the network cut, and state
# converges to the server after reconnect.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }
CL="python3 /cabinet/cabd-launch"

wait_online() {
    local want="$1" timeout="$2" t=0
    while true; do
        got=$(srv arcadectl --json status | python3 -c 'import sys,json; print(str(json.load(sys.stdin)["online"]).lower())')
        [ "$got" = "$want" ] && return 0
        t=$((t + 1)); [ "$t" -ge "$((timeout * 2))" ] && { echo "FAIL: online!=$want"; return 1; }
        sleep 0.5
    done
}

echo "== 0. stack up, online, state cached =="
compose up -d --build --wait --wait-timeout 300
compose restart cabinet >/dev/null 2>&1; sleep 2
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
wait_online true 30
t=0; until cab test -s /var/cache/arcade-state.json; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no state cache"; exit 1; }; sleep 0.5
done

echo "== 1. CUT THE NETWORK, then launch locally =="
tp toggle cab_link
wait_online false 10
cab rm -f /tmp/rendered/retroarch.cfg /tmp/rendered/snes.rmp
out=$(cab $CL snes /roms/snes/smw.sfc)
echo "$out" | grep -q '"ok": true' || { echo "FAIL: local launch failed offline: $out"; exit 1; }
cab sh -c 'xdotool search --name retroarch-fake >/dev/null' || { echo "FAIL: no game window (offline launch)"; exit 1; }
cab grep -q 'a = "b"' /tmp/rendered/snes.rmp || { echo "FAIL: configs not rendered offline"; exit 1; }

echo "== 2. local kill offline: frontend refocused =="
cab $CL kill >/dev/null
cab sh -c 'xdotool search --name retroarch-fake >/dev/null 2>&1' && { echo "FAIL: game survived local kill"; exit 1; } || true

echo "== 3. launch locally offline, reconnect: server view converges =="
cab $CL snes /roms/snes/smw.sfc >/dev/null
tp toggle cab_link
wait_online true 15
t=0; until srv arcadectl --json games running | grep -q 'snes/smw.sfc'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: server never saw locally-launched game"; exit 1; }; sleep 0.5
done

echo "== 4. remote kill of a locally-launched game =="
srv arcadectl games kill >/dev/null
t=0; until [ "$(srv arcadectl --json games running | python3 -c 'import sys,json; print(json.load(sys.stdin)["running"] or "")')" = "" ]; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: running never cleared"; exit 1; }; sleep 0.5
done
cab sh -c 'xdotool search --name retroarch-fake >/dev/null 2>&1' && { echo "FAIL: game survived remote kill"; exit 1; } || true

echo "TEST-LOCAL PASS"
