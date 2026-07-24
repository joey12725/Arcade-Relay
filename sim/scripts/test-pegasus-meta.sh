#!/usr/bin/env bash
# Pegasus collections/metadata generated from state (spec §5.3): per-system
# collections launching through cabd-launch, Favorites mirrored from state,
# Streaming and Steam collections, regenerated on state push.
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
t=0; until cab test -s /roms/gb/metadata.pegasus.txt; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: metadata never generated"; exit 1; }; sleep 0.5
done

echo "== 1. per-system collections launch through cabd-launch =="
cab grep -q "collection: GB" /roms/gb/metadata.pegasus.txt
cab grep -q "launch: python3 /cabinet/cabd-launch gb {file.path}" /roms/gb/metadata.pegasus.txt
for s in nes snes genesis arcade gba psx; do
    cab test -s "/roms/$s/metadata.pegasus.txt" || { echo "FAIL: $s collection missing"; exit 1; }
done

echo "== 2. favorites mirror: add via CLI -> appears in Favorites collection =="
srv arcadectl favorites add gb/loop.gb >/dev/null
t=0; until cab grep -q "file: /roms/gb/loop.gb" /roms/favorites/metadata.pegasus.txt 2>/dev/null; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: favorite never reached Pegasus metadata"; exit 1; }; sleep 0.5
done
cab grep -q "launch: python3 /cabinet/cabd-launch gb {file.path}" /roms/favorites/metadata.pegasus.txt
srv arcadectl favorites remove gb/loop.gb >/dev/null
t=0; until ! cab grep -q "loop.gb" /roms/favorites/metadata.pegasus.txt 2>/dev/null; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: favorite removal never propagated"; exit 1; }; sleep 0.5
done

echo "== 3. streaming + steam collections =="
cab grep -q 'launch: python3 /cabinet/cabd-launch stream "moonlight:Desktop"' /roms/streaming/metadata.pegasus.txt
cab grep -q 'launch: python3 /cabinet/cabd-launch stream "greenlight:Game Pass Cloud"' /roms/streaming/metadata.pegasus.txt
cab test -s "/roms/streaming/Game_Pass_Cloud.stream" || { echo "FAIL: stream stub missing"; exit 1; }
cab grep -q "game: Celeste" /roms/steam/metadata.pegasus.txt
cab grep -q "launch: python3 /cabinet/cabd-launch steam 504230" /roms/steam/metadata.pegasus.txt

echo "== 4. stomp: vandalized metadata regenerated on push =="
cab sh -c 'echo GARBAGE > /roms/gb/metadata.pegasus.txt'
srv arcadectl state push >/dev/null
t=0; until ! cab grep -q GARBAGE /roms/gb/metadata.pegasus.txt 2>/dev/null; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: metadata vandalism survived push"; exit 1; }; sleep 0.5
done
cab grep -q "collection: GB" /roms/gb/metadata.pegasus.txt

echo "== 5. theme files ship with the repo =="
cd ..
test -s themes/pegasus-redblue/theme.qml || { echo "FAIL: theme.qml missing"; exit 1; }
test -s themes/pegasus-redblue/theme.cfg || { echo "FAIL: theme.cfg missing"; exit 1; }
grep -q "muted: true" themes/pegasus-redblue/theme.qml || { echo "FAIL: trailers not muted in theme"; exit 1; }
grep -q "favorites" themes/pegasus-redblue/theme.qml || { echo "FAIL: no favorites-first logic"; exit 1; }

echo "TEST-PEGASUS-META PASS"
