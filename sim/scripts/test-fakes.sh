#!/usr/bin/env bash
# Unit test for the fake game binaries (A-8), run inside the sim cabinet
# container (read-only exec; does not touch the network link or heartbeats).
# Asserts: real X windows appear with the right titles; UDP QUIT works;
# SIGTERM works; --crash-after actually dies with the crash exit code.
set -euo pipefail
cd "$(dirname "$0")/.."

cab() { docker compose exec -T cabinet "$@"; }

docker compose up -d --wait --wait-timeout 120 cabinet
FK=/fakes
cab test -x $FK/retroarch || { echo "FAIL: $FK/retroarch not mounted/executable"; exit 1; }

echo "== 1. pegasus fake: window appears =="
cab sh -c "$FK/pegasus & echo \$! > /tmp/pegasus.pid"
cab sh -c 'for i in $(seq 1 20); do xdotool search --name pegasus-fake >/dev/null 2>&1 && exit 0; sleep 0.25; done; echo "FAIL: no pegasus-fake window"; exit 1'

echo "== 2. retroarch fake: window + UDP GET_STATUS =="
cab sh -c "$FK/retroarch /roms/snes/smw.sfc & echo \$! > /tmp/ra.pid"
cab sh -c 'for i in $(seq 1 20); do xdotool search --name "retroarch-fake" >/dev/null 2>&1 && exit 0; sleep 0.25; done; echo "FAIL: no retroarch-fake window"; exit 1'
st=$(cab python3 -c '
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(b"GET_STATUS", ("127.0.0.1", 55355))
print(s.recvfrom(1024)[0].decode().strip())')
echo "$st" | grep -q "PLAYING /roms/snes/smw.sfc" || { echo "FAIL: status '$st'"; exit 1; }

echo "== 3. UDP QUIT: process exits, window disappears =="
cab python3 -c '
import socket
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"QUIT", ("127.0.0.1", 55355))'
cab sh -c 'for i in $(seq 1 20); do xdotool search --name "retroarch-fake" >/dev/null 2>&1 || exit 0; sleep 0.25; done; echo "FAIL: retroarch-fake window still present after QUIT"; exit 1'

echo "== 4. --crash-after: exits nonzero by itself =="
rc=0
cab sh -c "timeout 15 $FK/retroarch /roms/snes/crash.sfc --crash-after 1; exit \$?" || rc=$?
[ "$rc" -eq 70 ] || { echo "FAIL: crash-after exit was $rc (want 70)"; exit 1; }
cab sh -c 'xdotool search --name "retroarch-fake" >/dev/null 2>&1 && { echo "FAIL: window survived crash"; exit 1; } || true'

echo "== 5. SIGTERM on pegasus fake: clean process exit =="
# cabd supervises its own pegasus-fake window in this container, so assert on
# OUR process exiting (kill -0), not on window-title absence.
cab sh -c 'kill $(cat /tmp/pegasus.pid)'
cab sh -c 'for i in $(seq 1 20); do kill -0 $(cat /tmp/pegasus.pid) 2>/dev/null || exit 0; sleep 0.25; done; echo "FAIL: pegasus fake still alive after SIGTERM"; exit 1'

echo "TEST-FAKES PASS"
