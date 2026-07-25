#!/usr/bin/env bash
# P8: the spec surfaces previously deferred in ASSUMPTIONS A-20 —
#   per-game mapping scope (§5.7 layer 3), synthetic input injection
#   (§5.10 input.press/input.text), lifeline recovery paths (§5.11),
#   CTL-3 interactive remapper + web UI library page (§6.3).
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
cab sh -c 'touch /roms/snes/smw.sfc /roms/snes/other.sfc'

echo "== 1. per-game scope: overrides system layer only for THAT rom =="
srv arcadectl controls map --scope system:snes --set a=b --set x=y >/dev/null
srv arcadectl controls map --scope game:snes/smw.sfc --set a=l >/dev/null
srv arcadectl --json state bundle | grep -q "controllers/games.yaml" \
    || { echo "FAIL: games.yaml not in bundle"; exit 1; }
srv arcadectl games launch --system snes --path /roms/snes/smw.sfc >/dev/null
cab grep -q 'a = "l"' /tmp/rendered/snes.rmp || { echo "FAIL: per-game override not rendered"; exit 1; }
cab grep -q 'x = "y"' /tmp/rendered/snes.rmp || { echo "FAIL: system layer lost under per-game"; exit 1; }
srv arcadectl games kill >/dev/null
srv arcadectl games launch --system snes --path /roms/snes/other.sfc >/dev/null
cab grep -q 'a = "b"' /tmp/rendered/snes.rmp || { echo "FAIL: non-override rom got the per-game map"; exit 1; }
srv arcadectl games kill >/dev/null

echo "== 2. per-game map is committed + survives a preset round-trip =="
srv arcadectl state log | grep -q "game:snes/smw.sfc" || { echo "FAIL: no per-game commit"; exit 1; }
srv arcadectl controls save-preset p8 >/dev/null
srv arcadectl controls map --scope game:snes/smw.sfc --set a=r >/dev/null
srv arcadectl controls load-preset p8 >/dev/null
srv arcadectl --json state bundle | python3 -c '
import json,sys
b = json.load(sys.stdin)
m = b["controllers/games.yaml"]["games"]["snes/smw.sfc"]["remap"]
assert m == {"a": "l"}, f"preset did not restore per-game layer: {m}"
print("preset restored per-game layer")'
rc=0; srv arcadectl controls map --scope game:bogus --set a=b 2>/dev/null || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: malformed game scope exited $rc, want 4"; exit 1; }

echo "== 3. input.press logical combo: kills a running game (no focus needed) =="
srv arcadectl games launch --system snes --path /roms/snes/smw.sfc >/dev/null
srv arcadectl --json status | grep -q '"running": "snes/smw.sfc"'
srv arcadectl --json cab press home | grep -q '"via": "combo"'
t=0; until srv arcadectl --json status | grep -q '"running": null'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: press home did not kill the game"; exit 1; }; sleep 0.5
done

echo "== 4. input.press/type reach X: xdotool drives a real window =="
cab sh -c 'command -v xdotool >/dev/null' || { echo "FAIL: no xdotool"; exit 1; }
srv arcadectl --json cab press ctrl+shift+F12 | grep -q '"via": "xdotool"' \
    || { echo "FAIL: key press not routed to xdotool"; exit 1; }
srv arcadectl --json cab type "hello arcade" | grep -q '"typed": 12' \
    || { echo "FAIL: type reported wrong length"; exit 1; }
rc=0; srv arcadectl cab press "not a key" 2>/dev/null || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: bogus key exited $rc, want 4 (must not silently succeed)"; exit 1; }

echo "== 5. lifeline recovery paths run and are idempotent =="
cab rm -f /tmp/transport-up /tmp/net-restarted
srv arcadectl --json cab lifeline on | grep -q '"lifeline": true'
t=0; until cab test -e /tmp/transport-up; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: fallback transport never brought up"; exit 1; }; sleep 0.5
done
# ssh path proven by its EFFECT: a real listener on :22 (checked via /proc)
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import lifeline
assert lifeline.ssh_listening(), 'ssh not listening after lifeline recovery'
print('ssh listener present')"
cab grep -q "recovery" /tmp/cabd.log || { echo "FAIL: recovery not logged"; exit 1; }
# idempotent: a second run reports already-listening, does not error
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import lifeline
r = lifeline.ensure_recovery_paths()
assert r['ssh'] == 'already listening', r
print('idempotent:', r)"

echo "== 6. panel button restarts networking while lifeline is up =="
cab sh -c 'echo "a down" > /tmp/input-events'
t=0; until cab test -e /tmp/net-restarted; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: panel button did not restart networking"; exit 1; }; sleep 0.5
done
srv arcadectl --json cab lifeline restart-network | grep -q '"restarted": true' \
    || { echo "FAIL: remote restart-network failed"; exit 1; }
srv arcadectl cab lifeline off >/dev/null

echo "== 7. web UI: interactive remapper + library page wired to real endpoints =="
srv python3 - <<'EOF'
import urllib.request
html = urllib.request.urlopen("http://localhost:8000/ui").read().decode()
for needed in ["function capture(", "function assign(", "/controls/monitor",
               "function loadLibrary(", "/games/list", "/favorites/",
               "id=\"libSystem\"", "renderTargets("]:
    assert needed in html, f"web UI missing {needed}"
print("web UI exposes the interactive remapper + library page")
EOF
srv python3 - <<'EOF'
import json, urllib.request
# the endpoints the new UI calls must actually answer
games = json.load(urllib.request.urlopen("http://localhost:8000/games/list"))
assert games["games"], "games list empty"
assert {"id", "system", "path", "favorite"} <= set(games["games"][0]), games["games"][0]
b = json.load(urllib.request.urlopen("http://localhost:8000/state/bundle"))
assert "controllers/global.yaml" in b
print(f"library endpoint ok: {len(games['games'])} games")
EOF

echo "TEST-P8 PASS"
