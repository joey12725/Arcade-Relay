#!/usr/bin/env bash
# M3 unit B: screensaver engine on real mpv streaming real 480p Jellyfin
# transcodes through toxiproxy. Asserts SCR-2 (zero-margin panscan via mpv
# osd-dimensions), SCR-3 (live brightness, playback uninterrupted), SCR-6
# (force on/off), SCR-7 (screensaver audio class muted), idle in/out.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

mpvipc() { # mpvipc '<json-command-array>' -> data field
    cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver, json
print(json.dumps(screensaver.ipc($1)))"
}

echo "== 0. fresh stack, sources enabled, long idle =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet jellyfin toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
t=0; until srv arcadectl screensaver sources 2>/dev/null | grep -q "Series A"; do
    t=$((t+1)); [ $t -ge 120 ] && { echo "FAIL: library never scanned"; exit 1; }; sleep 2
done
SA=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"]=="Series A"))')
srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl screensaver set --idle 3600 --brightness 80 >/dev/null
sleep 1

echo "== 1. force on: mpv streams through the tunnel, window up, status reflects =="
srv arcadectl screensaver on >/dev/null
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; echo no-window; exit 1'
t=0; until srv arcadectl --json status | grep -q '"screensaver": true'; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: status never showed screensaver"; exit 1; }; sleep 0.5
done

echo "== 2. SCR-2: fullscreen, panscan=1.0, ZERO letterbox margins =="
mpvipc '["get_property","panscan"]' | grep -q '"data": 1.0' || { echo "FAIL: panscan != 1.0"; exit 1; }
mpvipc '["get_property","fullscreen"]' | grep -q '"data": true' || { echo "FAIL: not fullscreen"; exit 1; }
cab python3 -c "
import sys, time; sys.path.insert(0, '/cabinet')
import screensaver
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    d = screensaver.get_prop('osd-dimensions')
    if d and d.get('w'):
        margins = [d.get(k, 1) for k in ('ml','mr','mt','mb')]
        # negative = video overflows (panscan crop), positive = black bars
        assert all(m <= 0 for m in margins), f'letterbox margins: {d}'
        assert d['w'] == 1024 and d['h'] == 768, f'not filling panel: {d}'
        print('no positive margins (crop-to-fill) at', d['w'], 'x', d['h'],
              'margins', margins); raise SystemExit(0)
    time.sleep(0.5)
print('FAIL: video never rendered'); raise SystemExit(1)"

echo "== 3. SCR-7: screensaver audio node muted =="
cab python3 -c "
import json, subprocess, time, sys
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    r = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
    for si in json.loads(r.stdout or '[]'):
        if (si.get('properties') or {}).get('media.role') == 'screensaver':
            if si['mute']: print('screensaver node muted'); sys.exit(0)
    time.sleep(0.2)
print('FAIL: screensaver node absent or unmuted'); sys.exit(1)"

echo "== 4. SCR-3: live brightness, <500ms engine apply, playback uninterrupted =="
cab python3 -c "
import sys, time; sys.path.insert(0, '/cabinet')
import screensaver
t_before = screensaver.get_prop('time-pos') or 0
t0 = time.monotonic()
screensaver.set_brightness(30)
dt = time.monotonic() - t0
vf = screensaver.get_prop('vf')
assert dt < 0.5, f'brightness apply took {dt:.3f}s'
assert any('0.3' in str(f.get('params', {})) for f in vf), f'vf not updated: {vf}'
time.sleep(1.0)
t_after = screensaver.get_prop('time-pos') or 0
assert t_after > t_before, f'playback stalled: {t_before} -> {t_after}'
print(f'brightness applied in {dt*1000:.0f}ms, playback continuous ({t_before:.1f}->{t_after:.1f}s)')"
srv arcadectl screensaver set --brightness 60 >/dev/null   # remote path too
sleep 1
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver
vf = screensaver.get_prop('vf')
assert any('0.6' in str(f.get('params', {})) for f in vf), f'remote brightness not applied: {vf}'
print('remote brightness change applied')"

echo "== 5. input exits screensaver instantly; frontend focused =="
t0=$(date +%s%3N)
cab sh -c 'echo "BTN_A down" > /tmp/input-events'
deadline=$((t0 + 3000)); ok=""
while [ "$(date +%s%3N)" -lt "$deadline" ]; do
    if ! cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1'; then ok=1; break; fi
    sleep 0.1
done
t1=$(date +%s%3N)
[ -n "$ok" ] || { echo "FAIL: screensaver survived input"; exit 1; }
echo "   input -> screensaver gone in $((t1 - t0))ms"
cab sh -c 'f=$(xdotool getwindowfocus) && p=$(xdotool search --name pegasus-fake | head -1) && [ "$f" = "$p" ]' || { echo "FAIL: frontend not focused"; exit 1; }
t=0; until srv arcadectl --json status | grep -q '"screensaver": false'; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: status stuck on"; exit 1; }; sleep 0.5
done

echo "== 6. idle timeout auto-starts it (idle=3s) =="
srv arcadectl screensaver set --idle 3 >/dev/null
sleep 1
cab sh -c 'echo "BTN_B down" > /tmp/input-events'   # reset idle clock
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo idle-never-started; exit 1'
echo "   idle screensaver started"

echo "== 7. game launch preempts screensaver =="
cab python3 /cabinet/cabd-launch snes /roms/snes/game.rom >/dev/null
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' && { echo "FAIL: screensaver survived game launch"; exit 1; } || true
cab python3 /cabinet/cabd-launch kill >/dev/null
srv arcadectl screensaver set --idle 3600 >/dev/null

echo "TEST-SCREENSAVER PASS"
