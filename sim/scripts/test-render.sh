#!/usr/bin/env bash
# CTL-4 / prime directive 3: configs render from the state repo before every
# launch, stomping any drift; state cache makes rendering fully offline-capable.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up =="
compose up -d --build --wait --wait-timeout 300
compose restart cabinet >/dev/null 2>&1; sleep 2
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. state pushed on connect; cache present on cabinet =="
t=0; until cab test -s /var/cache/arcade-state.json; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no state cache after connect"; exit 1; }; sleep 0.5
done
srv arcadectl state bundle | grep -q "controllers/global.yaml" || { echo "FAIL: bundle missing global.yaml"; exit 1; }

echo "== 2. launch renders configs from state (snes remap a->b) =="
srv arcadectl games launch --system snes --path /roms/snes/smw.sfc >/dev/null
cab grep -q 'input_player1_a = "a"' /tmp/rendered/retroarch.cfg || { echo "FAIL: global render wrong"; exit 1; }
cab grep -q 'a = "b"' /tmp/rendered/snes.rmp || { echo "FAIL: snes remap render wrong"; exit 1; }
cab grep -q "stomped" /tmp/rendered/snes.rmp || { echo "FAIL: header missing"; exit 1; }

echo "== 3. scramble rendered configs -> next launch stomps drift =="
cab sh -c 'echo "GARBAGE = scrambled" > /tmp/rendered/snes.rmp; echo "drift" >> /tmp/rendered/retroarch.cfg'
srv arcadectl games kill >/dev/null
srv arcadectl games launch --system snes --path /roms/snes/smw.sfc >/dev/null
cab grep -q "GARBAGE" /tmp/rendered/snes.rmp && { echo "FAIL: scramble survived relaunch"; exit 1; } || true
cab grep -q "drift" /tmp/rendered/retroarch.cfg && { echo "FAIL: drift survived relaunch"; exit 1; } || true
cab grep -q 'a = "b"' /tmp/rendered/snes.rmp || { echo "FAIL: clean render missing after stomp"; exit 1; }
srv arcadectl games kill >/dev/null

echo "== 4. offline render: partition + cabd restart, render still works from cache =="
tp toggle cab_link    # cut the server link
cab pkill -9 -f cabd.py || true
sleep 2               # supervisor respawn; cabd must load cache, offline
cab sh -c 'rm -f /tmp/rendered/*; python3 -c "
import json, sys
sys.path.insert(0, \"/cabinet\")
import renderer
bundle = json.load(open(\"/var/cache/arcade-state.json\"))
renderer.render(bundle, \"snes\")
"'
cab grep -q 'a = "b"' /tmp/rendered/snes.rmp || { echo "FAIL: offline render from cache broken"; exit 1; }
tp toggle cab_link    # restore link for whoever runs next
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 30 ] && { echo "FAIL: not back online after partition"; exit 1; }; sleep 0.5
done

echo "TEST-RENDER PASS"
