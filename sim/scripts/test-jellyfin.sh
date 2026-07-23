#!/usr/bin/env bash
# M3 unit A: Jellyfin adapter — bootstrap, enumeration, per-source
# enable/disable (SCR-4), 480p-capped transcode via the /media proxy on the
# tunnel path (SCR-1).
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up (jellyfin + server) =="
bash scripts/gen-media.sh
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet jellyfin toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. sources: library enumerated (bootstrap + scan may take a while) =="
t=0
until srv arcadectl screensaver sources 2>/dev/null | grep -q "Series A"; do
    t=$((t+1)); [ $t -ge 120 ] && { echo "FAIL: Series A never appeared"; srv arcadectl screensaver sources || true; exit 1; }
    sleep 2
done
srv arcadectl screensaver sources | python3 -c '
import sys, json
items = json.load(sys.stdin)
names = {i["name"] for i in items}
need = {"Series A", "Series B", "Redline (2009)"}
missing = need - names
assert not missing, f"missing {missing}: {names}"
assert all(not i["enabled"] for i in items), "nothing should be enabled yet"
print("enumerated:", sorted(names))'

echo "== 2. enable Series A + Redline; playlist resolves episodes =="
SA=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"]=="Series A"))')
RL=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"].startswith("Redline")))')
srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl screensaver enable-source "$RL" >/dev/null
srv arcadectl screensaver sources | python3 -c '
import sys, json
en = {i["name"] for i in json.load(sys.stdin) if i["enabled"]}
assert en == {"Series A", "Redline (2009)"}, en
print("enabled:", sorted(en))'
srv arcadectl screensaver playlist | python3 -c '
import sys, json
pl = json.load(sys.stdin)["playlist"]
assert len(pl) == 3, f"want 3 videos (2 eps + movie), got {len(pl)}: {pl}"
print("playlist:", len(pl), "videos")'
srv arcadectl state log | grep -q "enable-source" || { echo "FAIL: no git commit"; exit 1; }

echo "== 3. CABINET fetches a stream through the tunnel; transcode <=480p =="
URL=$(srv arcadectl screensaver playlist | python3 -c 'import sys,json; print(json.load(sys.stdin)["playlist"][0])')
cab sh -c "curl -fsS --max-time 60 -o /tmp/probe.mkv -r 0-2000000 'http://toxiproxy:8666$URL' || curl -fsS --max-time 60 -o /tmp/probe.mkv 'http://toxiproxy:8666$URL'"
cab sh -c 'test -s /tmp/probe.mkv' || { echo "FAIL: no media fetched"; exit 1; }
H=$(cab sh -c "mpv --no-config --frames=1 --vo=null --ao=null /tmp/probe.mkv --term-status-msg='' --msg-level=all=info 2>/dev/null | grep -oE 'VO: \[null\] [0-9]+x[0-9]+' | grep -oE '[0-9]+$' | head -1")
[ -n "$H" ] || { echo "FAIL: could not measure stream height"; exit 1; }
[ "$H" -le 480 ] || { echo "FAIL: stream height $H > 480"; exit 1; }
echo "   measured stream height: ${H}px (<=480)"

echo "== 4. disable-source removes from playlist =="
srv arcadectl screensaver disable-source "$RL" >/dev/null
srv arcadectl screensaver playlist | python3 -c '
import sys, json
pl = json.load(sys.stdin)["playlist"]
assert len(pl) == 2, f"want 2 after disabling movie, got {len(pl)}"
print("playlist after disable:", len(pl))'

echo "TEST-JELLYFIN PASS"
