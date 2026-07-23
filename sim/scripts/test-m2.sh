#!/usr/bin/env bash
# M2 control plane: mapping CLI, preset round-trip, state rollback,
# controls monitor. (Audio policy is covered by test-audio.)
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

launch_and_read() { # launch_and_read SYSTEM -> prints rendered .rmp content
    cab python3 /cabinet/cabd-launch "$1" "/roms/$1/game.rom" >/dev/null
    cab cat "/tmp/rendered/$1.rmp"
    cab python3 /cabinet/cabd-launch kill >/dev/null
}

echo "== 0. stack up (fresh statework seed) =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
t=0; until cab test -s /var/cache/arcade-state.json; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no cache"; exit 1; }; sleep 0.5
done

echo "== 1. baseline: snes renders a=b; save preset 'tourney' =="
launch_and_read snes | grep -q 'a = "b"' || { echo "FAIL: baseline render"; exit 1; }
srv arcadectl controls save-preset tourney >/dev/null
srv arcadectl controls list-presets | grep -q tourney || { echo "FAIL: preset not listed"; exit 1; }

echo "== 2. controls map changes the live mapping (a=x) =="
srv arcadectl controls map --scope system:snes --set a=x >/dev/null
sleep 1   # push propagation
launch_and_read snes | grep -q 'a = "x"' || { echo "FAIL: map change not rendered"; exit 1; }

echo "== 3. scramble rendered + load preset -> launch renders clean a=b =="
cab sh -c 'echo "GARBAGE" > /tmp/rendered/snes.rmp'
srv arcadectl controls load-preset tourney >/dev/null
sleep 1
out=$(launch_and_read snes)
echo "$out" | grep -q 'a = "b"' || { echo "FAIL: preset not restored ($out)"; exit 1; }
echo "$out" | grep -q GARBAGE && { echo "FAIL: scramble survived"; exit 1; } || true

echo "== 4. state log + rollback: volume change reverted =="
srv arcadectl volume set --master 40 >/dev/null
srv arcadectl --json volume get | grep -q '"master": 40' || { echo "FAIL: volume set"; exit 1; }
sha=$(srv arcadectl state log | python3 -c '
import sys, json
log = json.load(sys.stdin)["log"]
# first commit BEFORE the master=40 change
for i, line in enumerate(log):
    if "volume set master=40" in line:
        print(log[i+1].split()[0]); break')
[ -n "$sha" ] || { echo "FAIL: could not find pre-change commit"; exit 1; }
srv arcadectl state rollback "$sha" >/dev/null
srv arcadectl --json volume get | grep -q '"master": 70' || { echo "FAIL: rollback did not restore master=70"; exit 1; }
srv arcadectl state log | grep -q "rollback to" || { echo "FAIL: no rollback commit"; exit 1; }

echo "== 5. controls monitor sees injected button events =="
(srv arcadectl controls monitor --for 3 > /tmp/monitor.out) &
MONPID=$!
sleep 1
cab sh -c 'printf "BTN_A down\nBTN_A up\n" > /tmp/input-events'
wait $MONPID
grep -q "BTN_A down" /tmp/monitor.out || { echo "FAIL: monitor missed events ($(cat /tmp/monitor.out))"; exit 1; }

echo "TEST-M2 PASS"
