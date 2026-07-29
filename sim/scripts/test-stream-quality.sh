#!/usr/bin/env bash
# P10.5: per-app streaming quality (spec §5.8 "--resolution ... --fps 60").
# Streams ride the LAN, not the tunnel, so these are independent of the
# screensaver's 480p cap. Proof is the argv the client actually received.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. defaults from state reach moonlight's argv =="
cab rm -f /tmp/moonlight-argv /tmp/greenlight-argv
srv arcadectl --json stream launch "steam big picture" | grep -q '"running"' \
    || { echo "FAIL: moonlight launch failed"; exit 1; }
t=0; until cab test -e /tmp/moonlight-argv; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: moonlight never ran"; exit 1; }; sleep 0.5
done
ARGV=$(cab cat /tmp/moonlight-argv)
echo "   argv: $ARGV"
echo "$ARGV" | grep -q -- "--resolution 1920x1080" || { echo "FAIL: no resolution"; exit 1; }
echo "$ARGV" | grep -q -- "--fps 60" || { echo "FAIL: no fps"; exit 1; }
echo "$ARGV" | grep -q -- "--bitrate 20000" || { echo "FAIL: default bitrate wrong"; exit 1; }
srv arcadectl games kill >/dev/null

echo "== 2. per-app override wins over defaults (greenlight = 10000) =="
srv arcadectl --json stream launch "game pass" | grep -q '"running"' \
    || { echo "FAIL: greenlight launch failed"; exit 1; }
t=0; until cab test -e /tmp/greenlight-argv; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: greenlight never ran"; exit 1; }; sleep 0.5
done
GARGV=$(cab cat /tmp/greenlight-argv)
echo "   argv: $GARGV"
echo "$GARGV" | grep -q -- "--bitrate 10000" \
    || { echo "FAIL: per-app bitrate override not applied: $GARGV"; exit 1; }
echo "$GARGV" | grep -q -- "--resolution 1920x1080" \
    || { echo "FAIL: defaults not inherited alongside the override"; exit 1; }
srv arcadectl games kill >/dev/null

echo "== 3. a state change takes effect on the NEXT launch (no restart) =="
srv arcadectl stream set-quality "Steam Big Picture" --bitrate 5000 --fps 30 >/dev/null
t=0; until srv arcadectl --json stream apps | grep -q '"bitrate_kbps": 5000'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: quality change not stored"; exit 1; }; sleep 0.5
done
cab rm -f /tmp/moonlight-argv
srv arcadectl --json stream launch "steam big picture" >/dev/null
t=0; until cab test -e /tmp/moonlight-argv; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: relaunch never ran"; exit 1; }; sleep 0.5
done
NARGV=$(cab cat /tmp/moonlight-argv)
echo "   argv: $NARGV"
echo "$NARGV" | grep -q -- "--bitrate 5000" || { echo "FAIL: new bitrate not used: $NARGV"; exit 1; }
echo "$NARGV" | grep -q -- "--fps 30" || { echo "FAIL: new fps not used: $NARGV"; exit 1; }
srv arcadectl games kill >/dev/null

echo "== 4. the change is a git commit in the state repo =="
srv arcadectl state log | grep -q "stream set-quality" \
    || { echo "FAIL: quality change not committed"; exit 1; }

echo "== 5. invalid values are rejected, not silently stored =="
for bad in "--bitrate -5" "--fps 0"; do
    rc=0; srv arcadectl stream set-quality "Steam Big Picture" $bad >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 0 ] || { echo "FAIL: '$bad' was accepted"; exit 1; }
done

echo "TEST-STREAM-QUALITY PASS"
