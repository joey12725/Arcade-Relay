#!/usr/bin/env bash
# M3 acceptance (spec §8): SCR-1..7 each demonstrated; measured stream <=480p;
# zero letterbox/pillarbox pixels; brightness change <500ms without playback
# hiccup — with a screenshot mean-luma check (photons, not just engine state).
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== accept-m3: fresh stack =="
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

echo "== SCR-4/SCR-5: sources + params via CLI =="
SA=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"]=="Series A"))')
RL=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"].startswith("Redline")))')
srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl screensaver enable-source "$RL" >/dev/null
n1=$(srv arcadectl screensaver playlist | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["playlist"]))')
srv arcadectl screensaver disable-source "$RL" >/dev/null
n2=$(srv arcadectl screensaver playlist | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["playlist"]))')
[ "$n1" -gt "$n2" ] || { echo "FAIL: disable-source did not shrink playlist ($n1 -> $n2)"; exit 1; }
srv arcadectl screensaver set --idle 3600 --brightness 80 --mode shuffle_episodes >/dev/null
srv arcadectl --json screensaver status | grep -q '"brightness": 80' || { echo "FAIL: params not readable"; exit 1; }
echo "   sources toggled ($n1 -> $n2), params set+read via CLI"

echo "== SCR-1/SCR-6: force on -> streaming from Jellyfin over the tunnel =="
srv arcadectl screensaver on >/dev/null
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' || { echo "FAIL: no screensaver window"; exit 1; }

echo "== measured stream <=480p (mpv video-params of the LIVE stream) =="
cab python3 -c "
import sys, time; sys.path.insert(0, '/cabinet')
import screensaver
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    vp = screensaver.get_prop('video-params')
    if vp and vp.get('h'):
        assert vp['h'] <= 480, f'stream height {vp[\"h\"]} > 480'
        print('live stream:', vp['w'], 'x', vp['h']); raise SystemExit(0)
    time.sleep(0.5)
print('FAIL: no video params'); raise SystemExit(1)"

echo "== SCR-2: zero letterbox pixels (crop-to-fill margins) =="
cab python3 -c "
import sys, time; sys.path.insert(0, '/cabinet')
import screensaver
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    d = screensaver.get_prop('osd-dimensions')
    if d and d.get('w'):
        m = [d.get(k, 1) for k in ('ml','mr','mt','mb')]
        assert all(x <= 0 for x in m), f'letterbox: {d}'
        assert d['w'] == 1024 and d['h'] == 768, f'panel not filled: {d}'
        print('crop-to-fill margins', m); raise SystemExit(0)
    time.sleep(0.5)
raise SystemExit(1)"

echo "== SCR-3: brightness <500ms, luma actually drops, playback uninterrupted =="
cab python3 -c "
import sys, time, subprocess; sys.path.insert(0, '/cabinet')
import screensaver
from PIL import Image
def luma(path):
    img = Image.open(path).convert('L')
    px = list(img.getdata())
    return sum(px) / len(px)
screensaver.set_brightness(100)
time.sleep(0.5)
screensaver.ipc(['screenshot-to-file', '/tmp/shot-bright.png', 'video'])
t_before = screensaver.get_prop('time-pos') or 0
t0 = time.monotonic()
screensaver.set_brightness(20)
dt = time.monotonic() - t0
assert dt < 0.5, f'apply took {dt:.3f}s'
time.sleep(0.5)
screensaver.ipc(['screenshot-to-file', '/tmp/shot-dim.png', 'video'])
t_after = screensaver.get_prop('time-pos') or 0
lb, ld = luma('/tmp/shot-bright.png'), luma('/tmp/shot-dim.png')
assert ld < lb * 0.45, f'luma did not drop enough: {lb:.1f} -> {ld:.1f}'
assert t_after > t_before, f'playback stalled {t_before}->{t_after}'
print(f'apply {dt*1000:.0f}ms, luma {lb:.1f} -> {ld:.1f}, playback {t_before:.1f}->{t_after:.1f}s')"

echo "== SCR-7: screensaver audio muted =="
cab python3 -c "
import json, subprocess, time, sys
deadline = time.monotonic() + 10
while time.monotonic() < deadline:
    r = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
    for si in json.loads(r.stdout or '[]'):
        if (si.get('properties') or {}).get('media.role') == 'screensaver':
            if si['mute']: print('muted'); sys.exit(0)
    time.sleep(0.2)
print('FAIL: screensaver audio not muted'); sys.exit(1)"

echo "== SCR-6: force off via CLI; input path covered by idle test =="
srv arcadectl screensaver off >/dev/null
sleep 1
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' && { echo "FAIL: still on"; exit 1; } || true

echo "== idle in (3s) / input out =="
srv arcadectl screensaver set --idle 3 >/dev/null
sleep 1
cab sh -c 'echo "IDLE_RESET down" > /tmp/input-events'
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; exit 1' || { echo "FAIL: idle never started ss"; exit 1; }
cab sh -c 'echo "BTN_EXIT down" > /tmp/input-events'
cab sh -c 'for i in $(seq 1 20); do xdotool search --name arcade-screensaver >/dev/null 2>&1 || exit 0; sleep 0.25; done; exit 1' || { echo "FAIL: input did not exit ss"; exit 1; }
srv arcadectl screensaver set --idle 3600 >/dev/null

echo "ACCEPT-M3 PASS"
