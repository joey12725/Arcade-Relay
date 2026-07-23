#!/usr/bin/env bash
# M3 unit C: offline fallback + screensaver crash-heal + disable --for.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

ss_window() { cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1'; }

echo "== 0. fresh stack; source enabled; clips cache fills over the tunnel =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet jellyfin toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
t=0; until srv arcadectl screensaver sources 2>/dev/null | grep -q "Series A"; do
    t=$((t+1)); [ $t -ge 120 ] && { echo "FAIL: library never scanned"; exit 1; }; sleep 2
done
SA=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"]=="Series A"))')
srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl screensaver set --idle 3600 --brightness 80 >/dev/null
t=0; until [ "$(cab sh -c 'ls /var/cache/arcade-clips/*.ts 2>/dev/null | wc -l')" -ge 1 ]; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: clip cache never filled"; exit 1; }; sleep 2
done
echo "   clips cached: $(cab sh -c 'ls -la /var/cache/arcade-clips/ | grep -c .ts || true')"

echo "== 1. LINK CUT -> idle screensaver still starts, from local clips =="
srv arcadectl screensaver set --idle 3 >/dev/null
sleep 1
tp toggle cab_link          # cut
cab sh -c 'echo "BTN_A down" > /tmp/input-events'   # reset idle clock, offline
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo offline-ss-never-started; exit 1'
cab grep -q "local clips fallback" /tmp/cabd.log || { echo "FAIL: not the fallback path"; exit 1; }
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver
p = screensaver.get_prop('path')
assert p and p.startswith('/var/cache/arcade-clips/'), f'not playing a local clip: {p}'
print('playing local file:', p)"
cab sh -c 'echo "BTN_B down" > /tmp/input-events'   # exit it
sleep 2
tp toggle cab_link          # restore
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 30 ] && { echo "FAIL: not back online"; exit 1; }; sleep 0.5
done

echo "== 2. screensaver crash: watchdog clears flag, idle relaunches =="
srv arcadectl screensaver on >/dev/null
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1'
cab pkill -9 -f "[a]rcade-screensaver" || true
t=0; until srv arcadectl --json status | grep -q '"screensaver": false'; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: stale screensaver flag after crash"; exit 1; }; sleep 0.5
done
echo "   crash detected, flag cleared"
# idle=3 and no input since -> it must come back on its own
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo self-heal-relaunch-failed; exit 1'
echo "   self-healed: screensaver relaunched"

echo "== 3. disable --for suppresses idle relaunch; expiry restores =="
srv arcadectl screensaver disable --for 8s >/dev/null
ss_window && { echo "FAIL: disable did not stop screensaver"; exit 1; } || true
sleep 5   # idle=3 elapsed, but suppressed
ss_window && { echo "FAIL: screensaver started during suppression"; exit 1; } || true
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo post-suppression-relaunch-failed; exit 1'
echo "   suppression honored and expired correctly"

echo "== 4. cleanup =="
srv arcadectl screensaver set --idle 3600 >/dev/null
srv arcadectl screensaver off >/dev/null || true
sleep 1

echo "TEST-FALLBACK PASS"
