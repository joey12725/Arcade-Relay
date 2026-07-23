#!/usr/bin/env bash
# M4 acceptance (spec §8): `arcadectl roms request` for a known title results
# in the game launchable from the cabinet with art, no human touches.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== accept-m4: fresh stack =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== search finds the title =="
srv arcadectl roms search "metal slug 3" | grep -q "Metal Slug 3" || { echo "FAIL: search"; exit 1; }

echo "== one-liner request -> ready, no human touches =="
srv arcadectl roms request "metal slug 3" | grep -qE '"status": "(queued|downloading)"' || { echo "FAIL: request"; exit 1; }
t=0
until srv arcadectl roms status | grep -q '"status": "ready"'; do
    srv arcadectl roms status | grep -q '"status": "error"' && { echo "FAIL: pipeline error: $(srv arcadectl roms status)"; exit 1; }
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never ready: $(srv arcadectl roms status)"; exit 1; }
    sleep 1
done

echo "== ROM on cabinet, checksum-verified, art present, rescan fired =="
cab test -s /roms/arcade/Metal_Slug_3.bin || { echo "FAIL: rom missing on cabinet"; exit 1; }
SUM=$(cab sh -c 'sha256sum /roms/arcade/Metal_Slug_3.bin | cut -d" " -f1')
EXP=$(srv python3 -c "import sys; sys.path.insert(0,'/app'); import romdl, hashlib; print(hashlib.sha256(hashlib.sha256(b'ms3').digest()*8192).hexdigest())")
[ "$SUM" = "$EXP" ] || { echo "FAIL: checksum mismatch"; exit 1; }
cab test -s /roms/art/Metal_Slug_3.bin.png || { echo "FAIL: art missing"; exit 1; }
cab test -s /tmp/pegasus-rescan || { echo "FAIL: no rescan marker"; exit 1; }

echo "== launchable from the cabinet (local path) =="
out=$(cab python3 /cabinet/cabd-launch arcade /roms/arcade/Metal_Slug_3.bin)
echo "$out" | grep -q '"ok": true' || { echo "FAIL: launch: $out"; exit 1; }
cab sh -c 'xdotool search --name retroarch-fake >/dev/null' || { echo "FAIL: no game window"; exit 1; }
cab python3 /cabinet/cabd-launch kill >/dev/null

echo "ACCEPT-M4 PASS"
