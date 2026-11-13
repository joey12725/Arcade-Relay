#!/usr/bin/env bash
# M0 acceptance (spec §8): two criteria, run accelerated (docs/assumptions.md A-7/A-9):
#   1. Sustained heartbeat delivery >= 99% over a clean window.
#      Real bar: 24h @ 5s beats. Accelerated: ACCEPT_WINDOW_S (default 1200s)
#      @ 0.25s beats = 4800 expected beats, measured via arcaded's delivered-
#      beats counter (server side — delivery, not send attempts).
#   2. Unaided reconnect from "router reboot": toxiproxy container restarted
#      (its in-memory proxy config is wiped, link fully dead), proxy recreated
#      (= router finished booting), cabinet must come back with NO cabinet-side
#      intervention, within 60s.
set -euo pipefail
cd "$(dirname "$0")/.."

W="${ACCEPT_WINDOW_S:-1200}"
HB=0.25

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

field() { srv arcadectl --json status | python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"; }

wait_online() {
    local want="$1" timeout="$2" t=0
    while true; do
        [ "$(field online | tr 'A-Z' 'a-z')" = "$want" ] && return 0
        t=$((t + 1))
        [ "$t" -ge "$((timeout * 2))" ] && { echo "FAIL: online!=$want after ${timeout}s"; return 1; }
        sleep 0.5
    done
}

echo "== accept-m0: stack up =="
compose up -d --build --wait --wait-timeout 300
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
wait_online true 30

echo "== criterion 1: >=99% heartbeat delivery over ${W}s clean window =="
b0=$(field beats)
sleep "$W"
b1=$(field beats)
delivered=$((b1 - b0))
expected=$(python3 -c "print(int($W / $HB))")
ratio=$(python3 -c "print($delivered / $expected)")
echo "delivered=$delivered expected=$expected ratio=$ratio"
python3 -c "import sys; sys.exit(0 if $delivered / $expected >= 0.99 else 1)" \
    || { echo "FAIL: delivery ratio $ratio < 0.99"; exit 1; }

echo "== criterion 2: router reboot -> unaided reconnect =="
connects_before=$(field connects)
compose restart toxiproxy         # in-memory proxy config wiped: link is dead
wait_online false 15              # outage must be detected
tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link   # router back up
wait_online true 60
connects_after=$(field connects)
[ "$connects_after" -gt "$connects_before" ] || { echo "FAIL: no new WS connection after reboot"; exit 1; }

echo "== criterion 2b: link still does work (exec round-trip) =="
out=$(srv arcadectl cab exec -- echo accept-m0)
[ "$out" = "accept-m0" ] || { echo "FAIL: exec got '$out'"; exit 1; }

echo "ACCEPT-M0 PASS (window=${W}s delivered=$delivered/$expected ratio=$ratio)"
