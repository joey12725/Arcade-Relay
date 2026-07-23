#!/usr/bin/env bash
# UI-2/SRV-2: favorites are server-authoritative, each change is a git
# commit in the state repo, and the change lands in the cabinet's cache.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up =="
compose up -d --build --wait --wait-timeout 300
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. add favorite -> listed =="
srv arcadectl favorites add snes/smw.sfc | grep -q "snes/smw.sfc" || { echo "FAIL: add"; exit 1; }
srv arcadectl favorites list | grep -q "snes/smw.sfc" || { echo "FAIL: list"; exit 1; }

echo "== 2. change is a git commit in the state repo =="
srv arcadectl state log | grep -q "favorite add snes/smw.sfc" || { echo "FAIL: no git commit"; exit 1; }

echo "== 3. change reaches the cabinet cache (push after change) =="
t=0; until cab grep -q "snes/smw.sfc" /var/cache/arcade-state.json 2>/dev/null; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: favorite never reached cabinet cache"; exit 1; }; sleep 0.5
done

echo "== 4. remove -> gone everywhere =="
srv arcadectl favorites remove snes/smw.sfc >/dev/null
srv arcadectl favorites list | grep -q "snes/smw.sfc" && { echo "FAIL: remove"; exit 1; } || true
srv arcadectl state log | grep -q "favorite remove snes/smw.sfc" || { echo "FAIL: no remove commit"; exit 1; }
t=0; until ! cab grep -q "snes/smw.sfc" /var/cache/arcade-state.json 2>/dev/null; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: removal never reached cabinet"; exit 1; }; sleep 0.5
done

echo "TEST-FAVORITES PASS"
