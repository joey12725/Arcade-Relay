#!/usr/bin/env bash
# P8: the spec surfaces previously deferred in docs/assumptions.md A-20 —
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
for bad in "game:bogus" "game:../../etc/passwd" "game:snes/sub/dir.sfc" "game:/x"; do
    rc=0; srv arcadectl controls map --scope "$bad" --set a=b 2>/dev/null || rc=$?
    [ "$rc" -eq 4 ] || { echo "FAIL: scope '$bad' exited $rc, want 4"; exit 1; }
done
srv test -e /statework/controllers/passwd && { echo "FAIL: traversal wrote in state repo"; exit 1; } || true

echo "== 2b. pre-P8 preset (no games key) must NOT wipe per-game maps =="
srv python3 - <<'EOF'
import yaml, pathlib
# a preset saved before the per-game layer existed
p = pathlib.Path("/statework/controllers/presets/legacy.yaml")
snap = yaml.safe_load(pathlib.Path("/statework/controllers/presets/p8.yaml").read_text())
snap.pop("games", None)
p.write_text(yaml.safe_dump(snap))
print("wrote legacy preset without a games key")
EOF
srv arcadectl controls load-preset legacy >/dev/null
srv arcadectl --json state bundle | python3 -c '
import json,sys
m = json.load(sys.stdin)["controllers/games.yaml"]["games"]["snes/smw.sfc"]["remap"]
assert m == {"a": "l"}, f"legacy preset wiped the per-game layer: {m}"
print("legacy preset left the per-game layer intact")'

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
# an IPv6-only sshd must also count (Debian binds :: by default). The sim
# kernel has IPv6 off, so feed the parser a real-format tcp6 table.
cab python3 - <<'EOF'
import sys, tempfile, os
sys.path.insert(0, '/cabinet')
import lifeline
tcp6 = ("  sl  local_address remote_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
        "   0: 00000000000000000000000000000000:0016 00000000000000000000000000000000:0000 0A "
        "00000000:00000000 00:00000000 00000000     0        0 12345 1 0000 100 0\n")
with tempfile.NamedTemporaryFile("w", suffix="tcp6", delete=False) as f:
    f.write(tcp6); path = f.name
empty = tempfile.NamedTemporaryFile("w", suffix="tcp", delete=False)
empty.write("  sl  local_address remote_address st\n"); empty.close()
assert lifeline.ssh_listening(22, (empty.name, path)), "IPv6-only sshd not detected"
assert not lifeline.ssh_listening(22, (empty.name,)), "false positive on empty table"
os.unlink(path); os.unlink(empty.name)
print("tcp6-only listener detected (and no false positive)")
EOF

echo "== 6. panel button restarts networking while lifeline is up =="
cab sh -c 'echo "a down" > /tmp/input-events'
t=0; until cab test -e /tmp/net-restarted; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: panel button did not restart networking"; exit 1; }; sleep 0.5
done
srv arcadectl --json cab lifeline restart-network | grep -q '"restarted": true' \
    || { echo "FAIL: remote restart-network failed"; exit 1; }
srv arcadectl cab lifeline off >/dev/null

echo "== 6b. concurrent lifeline on/off leaves no orphan overlay =="
for i in 1 2 3 4 5 6; do
    srv arcadectl cab lifeline on >/dev/null 2>&1 &
    srv arcadectl cab lifeline off >/dev/null 2>&1 &
done
wait
srv arcadectl cab lifeline off >/dev/null 2>&1 || true
t=0; until ! cab sh -c 'xdotool search --name arcade-lifeline >/dev/null 2>&1'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: orphaned lifeline overlay survived off"; exit 1; }; sleep 0.5
done
cab pgrep -f "[-]-title=arcade-lifeline" >/dev/null \
    && { echo "FAIL: orphaned lifeline mpv process"; exit 1; } || true
srv arcadectl --json status | grep -q '"online": true' \
    || { echo "FAIL: cabinet not healthy after lifeline storm"; exit 1; }

echo "== 6c. /cab/input rejects non-string params with 400, not a raw 500 =="
srv python3 - <<'EOF'
import json, urllib.request, urllib.error
for bad in ({"key": 123}, {"key": None}, {"text": ["x"]}, {"key": ""}, {}):
    req = urllib.request.Request("http://localhost:8000/cab/input",
                                 data=json.dumps(bad).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise SystemExit(f"FAIL: {bad} was accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"{bad} -> {e.code}, want 400"
print("malformed input params rejected with 400")
EOF

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
