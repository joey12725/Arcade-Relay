#!/usr/bin/env bash
# M1 link test: full remote game lifecycle through arcadectl.
# Launch -> running state visible; kill -> frontend focused; kill -9 ->
# frontend refocused <2s (EMU-3, timed externally); hang -> auto-killed and
# recovered; frontend death -> respawned by cabd.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

running() { srv arcadectl --json games running | python3 -c 'import sys,json; print(json.load(sys.stdin)["running"] or "")'; }

wait_running() { # wait_running EXPECT TIMEOUT_S  (EXPECT="" for nothing)
    local want="$1" timeout="$2" t=0
    while true; do
        [ "$(running)" = "$want" ] && return 0
        t=$((t + 1))
        [ "$t" -ge "$((timeout * 4))" ] && { echo "FAIL: running!='$want' after ${timeout}s (got '$(running)')"; return 1; }
        sleep 0.25
    done
}

frontend_focused() {
    cab sh -c 'f=$(xdotool getwindowfocus 2>/dev/null) || exit 1
               p=$(xdotool search --name pegasus-fake 2>/dev/null | head -1) || exit 1
               [ -n "$f" ] && [ "$f" = "$p" ]'
}

echo "== 0. stack up (fresh cabd with launcher integration) =="
compose up -d --build --wait --wait-timeout 300
compose restart cabinet >/dev/null 2>&1
sleep 2
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: cabinet never online"; exit 1; }; sleep 0.5
done

echo "== 1. cabd supervises the frontend: pegasus window present =="
cab sh -c 'for i in $(seq 1 40); do xdotool search --name pegasus-fake >/dev/null 2>&1 && exit 0; sleep 0.25; done; echo "no pegasus-fake window"; exit 1'

echo "== 2. games launch: runs, visible in status, window up =="
out=$(srv arcadectl --json games launch --system snes --path /roms/snes/smw.sfc)
echo "$out" | grep -q '"running": "snes/smw.sfc"' || { echo "FAIL: launch returned $out"; exit 1; }
cab sh -c 'xdotool search --name retroarch-fake >/dev/null' || { echo "FAIL: no game window"; exit 1; }
wait_running "snes/smw.sfc" 5

echo "== 3. games kill: frontend refocused, state cleared =="
srv arcadectl games kill >/dev/null
wait_running "" 5
frontend_focused || { echo "FAIL: frontend not focused after kill"; exit 1; }
cab sh -c 'xdotool search --name retroarch-fake >/dev/null 2>&1' && { echo "FAIL: game window survived kill"; exit 1; } || true

echo "== 4. kill -9 the emulator: frontend refocused within 2s (external clock) =="
srv arcadectl games launch --system snes --path /roms/snes/smw.sfc >/dev/null
wait_running "snes/smw.sfc" 5
t0=$(date +%s%3N)
cab pkill -9 -f "/fakes/retroarch" || true
deadline=$((t0 + 2000)); ok=""
while [ "$(date +%s%3N)" -lt "$deadline" ]; do
    if frontend_focused; then ok=1; break; fi
    sleep 0.1
done
t1=$(date +%s%3N)
[ -n "$ok" ] || { echo "FAIL: frontend not focused within 2s of kill -9"; exit 1; }
echo "   kill-9 -> frontend focused in $((t1 - t0))ms"
wait_running "" 5

echo "== 5. hung game: watchdog kills and recovers =="
srv arcadectl games launch --system snes --path /roms/snes/hang.sfc --args "--hang-after 0.3" >/dev/null
wait_running "snes/hang.sfc" 5
wait_running "" 8    # 2s silence threshold + kill escalation + margin
frontend_focused || { echo "FAIL: frontend not focused after hang recovery"; exit 1; }
cab sh -c 'pgrep -f "[h]ang.sfc" >/dev/null 2>&1' && { echo "FAIL: hung game still alive"; exit 1; } || true

echo "== 6. frontend death: cabd respawns it =="
cab pkill -f "/fakes/pegasus" || true
cab sh -c 'for i in $(seq 1 16); do xdotool search --name pegasus-fake >/dev/null 2>&1 && exit 0; sleep 0.25; done; echo "frontend not respawned within 4s"; exit 1'

echo "TEST-M1 PASS"
