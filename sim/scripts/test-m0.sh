#!/usr/bin/env bash
# M0 link test: real arcaded + cabd over toxiproxy.
# Asserts: online status via heartbeats; exec RPC round-trip; offline detection
# after partition (3 missed beats); unaided reconnect after partition ends.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

wait_online() { # wait_online EXPECT(true|false) TIMEOUT_S
    local want="$1" timeout="$2" t=0
    while true; do
        got=$(srv arcadectl --json status | python3 -c 'import sys,json; print(str(json.load(sys.stdin)["online"]).lower())')
        [ "$got" = "$want" ] && return 0
        t=$((t + 1))
        if [ "$t" -ge "$((timeout * 2))" ]; then
            echo "FAIL: online!=$want after ${timeout}s"; return 1
        fi
        sleep 0.5
    done
}

echo "== 0. stack up =="
compose up -d --build --wait --wait-timeout 300
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q enabled=false && tp toggle cab_link || true

echo "== 1. cabinet comes online (heartbeats through toxiproxy) =="
wait_online true 20

echo "== 2. exec RPC round-trip =="
out=$(srv arcadectl cab exec -- echo m0-roundtrip)
[ "$out" = "m0-roundtrip" ] || { echo "FAIL: exec got '$out'"; exit 1; }
# exit code propagation: failing remote command must exit non-zero
if srv arcadectl cab exec -- false 2>/dev/null; then
    echo "FAIL: remote 'false' reported success"; exit 1
fi

echo "== 3. partition -> offline after 3 missed beats =="
tp toggle cab_link
# hb=0.25s => offline threshold 0.75s; allow detection margin
sleep 3
wait_online false 5

echo "== 4. link restored -> unaided reconnect =="
tp toggle cab_link
wait_online true 15   # backoff cap 2s in sim; must recover well within this

echo "== 5. cabd survived: exec works again =="
out=$(srv arcadectl cab exec -- echo back-again)
[ "$out" = "back-again" ] || { echo "FAIL: post-reconnect exec got '$out'"; exit 1; }

echo "TEST-M0 PASS"
