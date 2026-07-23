#!/usr/bin/env bash
# Smoke test for the sim rig (ROADMAP M-SIM). Asserts:
#  1. all containers up and healthy
#  2. cabinet reaches server ONLY via toxiproxy (direct route must fail)
#  3. chaos is injectable: latency toxic applies; partition (proxy disable)
#     breaks the link; re-enable restores it
#  4. cabinet AV stack is real: X display, mpv, PipeWire all answer
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 1. containers healthy =="
compose up -d --build --wait --wait-timeout 300

echo "== 2. link only via toxiproxy =="
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
cab curl -fsS -o /dev/null --max-time 5 http://toxiproxy:8666/
if cab curl -fsS -o /dev/null --max-time 3 http://server:8000/ 2>/dev/null; then
    echo "FAIL: cabinet can bypass toxiproxy and reach server directly"; exit 1
fi

echo "== 3. chaos injectable =="
tp toxic add --type latency --attribute latency=500 cab_link
t0=$(date +%s%3N)
cab curl -fsS -o /dev/null --max-time 10 http://toxiproxy:8666/
t1=$(date +%s%3N)
tp toxic remove --toxicName latency_downstream cab_link
elapsed=$((t1 - t0))
if [ "$elapsed" -lt 400 ]; then
    echo "FAIL: latency toxic had no effect (request took ${elapsed}ms, expected >=400ms)"; exit 1
fi
echo "latency toxic verified: request took ${elapsed}ms"
tp toggle cab_link   # disable -> partition
if cab curl -fsS -o /dev/null --max-time 3 http://toxiproxy:8666/ 2>/dev/null; then
    echo "FAIL: partition did not break the link"; exit 1
fi
tp toggle cab_link   # re-enable
cab curl -fsS -o /dev/null --max-time 5 http://toxiproxy:8666/

echo "== 4. cabinet AV stack real =="
cab sh -c 'xdpyinfo -display :0 >/dev/null'
# mpv must actually render a frame onto the Xvfb display, not just execute
cab sh -c 'mpv --no-config --vo=x11 --frames=1 av://lavfi:testsrc >/dev/null 2>&1'
cab sh -c 'wpctl status >/dev/null'

echo "SIM-TEST PASS"
