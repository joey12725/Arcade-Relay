#!/usr/bin/env bash
# CLI completeness (spec §6/§10 agent-first surface): games list/fuzzy launch,
# favorites by query, controls diff, roms sync, stream apps/launch, cab logs,
# state diff/bundle, screensaver toggle. Every command through arcadectl only.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up (jellyfin too: screensaver toggle needs a source) =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet jellyfin toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
cab sh -c 'touch /roms/snes/smw.sfc /roms/snes/mario_kart.sfc /roms/gb/loop.gb'

echo "== 1. games list: full, --system filter, favorites star =="
srv arcadectl games list | grep -q "snes/smw.sfc"
srv arcadectl games list | grep -q "gb/loop.gb"
srv arcadectl games list --system snes | grep -q "snes/mario_kart.sfc"
if srv arcadectl games list --system snes | grep -q "gb/"; then
    echo "FAIL: --system snes leaked gb entries"; exit 1
fi

echo "== 2. favorites by fuzzy query (no slash) =="
srv arcadectl favorites add loop | grep -q "gb/loop.gb"
srv arcadectl games list --favorites | grep -qx "\* gb/loop.gb"
if srv arcadectl games list --favorites | grep -q "snes/"; then
    echo "FAIL: --favorites leaked non-favorites"; exit 1
fi
srv arcadectl favorites remove loop >/dev/null
srv arcadectl --json favorites list | grep -vq "loop.gb"

echo "== 3. fuzzy launch: unique hit launches, ambiguous exits 4 =="
srv arcadectl --json games launch kart | grep -q '"running": "snes/mario_kart.sfc"'
srv arcadectl games kill >/dev/null
rc=0; srv arcadectl games launch sfc 2>/tmp/amb.err || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: ambiguous query exited $rc, want 4"; exit 1; }
grep -q "smw.sfc" /tmp/amb.err && grep -q "mario_kart.sfc" /tmp/amb.err \
    || { echo "FAIL: candidates not listed"; cat /tmp/amb.err; exit 1; }

echo "== 4. --exact: filename matches, substring does not =="
rc=0; srv arcadectl games launch smw --exact 2>/dev/null || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: --exact 'smw' exited $rc, want 4"; exit 1; }
srv arcadectl --json games launch smw.sfc --exact | grep -q '"running": "snes/smw.sfc"'
srv arcadectl games kill >/dev/null

echo "== 5. controls diff between presets =="
srv arcadectl controls save-preset p1 >/dev/null
srv arcadectl controls map --scope global --set a=CLITESTTARGET >/dev/null
srv arcadectl controls save-preset p2 >/dev/null
out=$(srv arcadectl --json controls diff p1 p2)
echo "$out" | grep -q "global.buttons.a" || { echo "FAIL: diff missed the change: $out"; exit 1; }
echo "$out" | grep -q "CLITESTTARGET" || { echo "FAIL: diff missing new value: $out"; exit 1; }
srv arcadectl --json controls diff p1 p1 | grep -q '"diff": {}' \
    || { echo "FAIL: identical presets show a diff"; exit 1; }

echo "== 6. stream apps + fuzzy stream launch =="
srv arcadectl --json stream apps | grep -q "Game Pass Cloud"
srv arcadectl --json stream apps | grep -q "Steam Big Picture"
srv arcadectl --json stream launch "game pass" | grep -q '"running": "stream/greenlight:Game Pass Cloud"'
srv arcadectl games kill >/dev/null

echo "== 7. cab logs: ring buffer + follow mode =="
srv arcadectl cab logs --lines 500 | grep -q "state synced" \
    || { echo "FAIL: logs missing known cabd line"; exit 1; }
srv sh -c 'timeout 4 arcadectl cab logs -f > /tmp/follow.out' || true
srv test -s /tmp/follow.out || { echo "FAIL: logs -f produced nothing"; exit 1; }

echo "== 8. roms sync: exit 0, cabinet rescan actually triggered =="
before=$(cab sh -c '{ wc -l < /tmp/pegasus-rescan; } 2>/dev/null || echo 0')
srv arcadectl --json roms sync | grep -q '"rescan": true'
after=$(cab sh -c 'wc -l < /tmp/pegasus-rescan 2>/dev/null || echo 0')
[ "$after" -gt "$before" ] || { echo "FAIL: rescan marker did not grow"; exit 1; }

echo "== 9. state diff + bundle =="
srv arcadectl volume set --master 41 >/dev/null
srv arcadectl state diff | grep -q "audio.yaml" || { echo "FAIL: diff missing audio.yaml"; exit 1; }
srv arcadectl state diff | grep -q "master: 41" || { echo "FAIL: diff missing new value"; exit 1; }
srv arcadectl --json state bundle | grep -q "cabinet.yaml"

echo "== 10. screensaver toggle: off->on->off =="
t=0; until srv arcadectl screensaver sources 2>/dev/null | grep -q "Series A"; do
    t=$((t+1)); [ $t -ge 120 ] && { echo "FAIL: jellyfin sources never appeared"; exit 1; }; sleep 1
done
SA=$(srv arcadectl screensaver sources | python3 -c 'import sys,json;
print(next(i["id"] for i in json.load(sys.stdin) if i["name"]=="Series A"))')
srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl --json status | grep -q '"screensaver": false' \
    || { echo "FAIL: screensaver unexpectedly on before toggle"; exit 1; }
srv arcadectl screensaver toggle >/dev/null
t=0; until srv arcadectl --json status | grep -q '"screensaver": true'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: toggle never turned it on"; exit 1; }; sleep 0.5
done
srv arcadectl screensaver toggle >/dev/null
t=0; until srv arcadectl --json status | grep -q '"screensaver": false'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: toggle never turned it off"; exit 1; }; sleep 0.5
done

echo "TEST-CLI-COMPLETE PASS"
