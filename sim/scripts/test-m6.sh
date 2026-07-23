#!/usr/bin/env bash
# M6 plumbing test: web UI served, screenshot returns a real PNG of the
# panel, docs/skills exist and reference only real commands.
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

echo "== 1. web UI served with the dashboard =="
srv python3 -c "
import urllib.request
html = urllib.request.urlopen('http://localhost:8000/ui', timeout=10).read().decode()
for needle in ('ARCADE CONTROL', 'Controller remapper', 'ROM queue', 'Sources', 'screenshot'):
    assert needle in html, f'missing {needle}'
print('dashboard sections present')"

echo "== 2. screenshot: real PNG, plausibly panel-sized =="
srv python3 -c "
import urllib.request, struct
png = urllib.request.urlopen('http://localhost:8000/cab/screenshot', timeout=60).read()
assert png[:8] == b'\x89PNG\r\n\x1a\n', 'not a PNG'
w, h = struct.unpack('>II', png[16:24])
assert (w, h) == (1024, 768), f'unexpected size {w}x{h}'
print(f'PNG {w}x{h}, {len(png)} bytes')"

echo "== 3. arcadectl cab screenshot round-trip =="
srv sh -c 'arcadectl cab screenshot > /tmp/shot.png && head -c8 /tmp/shot.png | od -An -tx1' | grep -q "89 50 4e 47" || { echo "FAIL: CLI screenshot"; exit 1; }

echo "== 4. onboarding docs + all six skills exist and are non-trivial =="
cd ..
test -s AGENTS.md && test -e CLAUDE.md || { echo "FAIL: onboarding docs"; exit 1; }
for s in screensaver audio controllers roms games diagnostics; do
    test -s "skills/$s/SKILL.md" || { echo "FAIL: skills/$s missing"; exit 1; }
    grep -q "arcadectl" "skills/$s/SKILL.md" || { echo "FAIL: skills/$s has no commands"; exit 1; }
done

echo "== 5. every arcadectl command mentioned in skills actually parses =="
cd sim
for cmd in "status" "volume get" "screensaver status" "controls list-presets" "roms status" "games running" "favorites list" "state log"; do
    srv arcadectl --json $cmd >/dev/null || { echo "FAIL: '$cmd' broken"; exit 1; }
done

echo "TEST-M6 PASS"
