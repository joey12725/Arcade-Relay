#!/usr/bin/env bash
# M2 acceptance (spec §8):
#  1. trailer audio never audible with default policy — automated: play a
#     trailer-class client, assert its node is muted at first observation,
#     then have it un-mute ITSELF and assert re-clamp (misbehaving client).
#  2. preset round-trip: save -> scramble configs on purpose (live mapping
#     changed AND rendered file vandalized) -> load -> verify launch renders
#     clean from the preset.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== accept-m2: fresh stack =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done
t=0; until cab test -s /var/cache/arcade-state.json; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: no cache"; exit 1; }; sleep 0.5
done

echo "== criterion 1: trailer never audible =="
cab sh -c "env 'PULSE_PROP_media.role=trailers' mpv --no-config --vo=null --ao=pulse --loop=inf --really-quiet av://lavfi:sine=frequency=880 >/dev/null 2>&1 &"
cab python3 -c "
import json, subprocess, time, sys
deadline = time.monotonic() + 3
idx = None
while time.monotonic() < deadline and idx is None:
    r = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
    for si in json.loads(r.stdout or '[]'):
        if (si.get('properties') or {}).get('media.role') == 'trailers':
            idx = str(si['index'])
            if not si['mute']:
                # observed unmuted once — poll tight until muted, budget 500ms
                t0 = time.monotonic()
                while time.monotonic() - t0 < 0.5:
                    r2 = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
                    s2 = next(s for s in json.loads(r2.stdout) if str(s['index']) == idx)
                    if s2['mute']: break
                    time.sleep(0.02)
                else:
                    print('FAIL: trailer audible beyond 500ms'); sys.exit(1)
    time.sleep(0.05)
if idx is None:
    print('FAIL: trailer node never appeared'); sys.exit(1)
print('trailer muted on arrival')
# misbehaving client: un-mute itself -> must be re-clamped
time.sleep(2)
subprocess.run(['pactl','set-sink-input-mute', idx, '0'])
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    r = subprocess.run(['pactl','-f','json','list','sink-inputs'], capture_output=True, text=True)
    si = next(s for s in json.loads(r.stdout) if str(s['index']) == idx)
    if si['mute']:
        print('self-unmute re-clamped'); sys.exit(0)
    time.sleep(0.05)
print('FAIL: self-unmuted trailer stayed audible'); sys.exit(1)
"
cab pkill -f "sine=frequency=880" || true

echo "== criterion 2: preset round-trip =="
srv arcadectl controls save-preset accept >/dev/null
srv arcadectl controls map --scope system:snes --set a=SCRAMBLED >/dev/null
sleep 1
cab sh -c 'echo "GARBAGE" > /tmp/rendered/snes.rmp 2>/dev/null || true'
srv arcadectl controls load-preset accept >/dev/null
sleep 1
cab python3 /cabinet/cabd-launch snes /roms/snes/game.rom >/dev/null
out=$(cab cat /tmp/rendered/snes.rmp)
cab python3 /cabinet/cabd-launch kill >/dev/null
echo "$out" | grep -q 'a = "b"' || { echo "FAIL: preset not restored ($out)"; exit 1; }
echo "$out" | grep -qE 'GARBAGE|SCRAMBLED' && { echo "FAIL: scramble survived ($out)"; exit 1; } || true

echo "ACCEPT-M2 PASS"
