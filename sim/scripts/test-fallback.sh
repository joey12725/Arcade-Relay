#!/usr/bin/env bash
# M3 unit C: offline fallback + screensaver crash-heal + disable --for.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

ss_window() { cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1'; }

echo "== 0. fresh stack; source enabled; clips cache fills over the tunnel =="
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
t=0; until [ "$(cab sh -c 'ls /var/cache/arcade-clips/*.ts 2>/dev/null | wc -l')" -ge 1 ]; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: clip cache never filled"; exit 1; }; sleep 2
done
echo "   clips cached: $(cab sh -c 'ls -la /var/cache/arcade-clips/ | grep -c .ts || true')"

echo "== 1. LINK CUT -> idle screensaver still starts, from local clips =="
srv arcadectl screensaver set --idle 3 >/dev/null
sleep 1
tp toggle cab_link          # cut
cab sh -c 'echo "BTN_A down" > /tmp/input-events'   # reset idle clock, offline
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo offline-ss-never-started; exit 1'
cab grep -q "local clips fallback" /tmp/cabd.log || { echo "FAIL: not the fallback path"; exit 1; }
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver
p = screensaver.get_prop('path')
assert p and p.startswith('/var/cache/arcade-clips/'), f'not playing a local clip: {p}'
print('playing local file:', p)"
cab sh -c 'echo "BTN_B down" > /tmp/input-events'   # exit it
sleep 2
tp toggle cab_link          # restore
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 30 ] && { echo "FAIL: not back online"; exit 1; }; sleep 0.5
done

echo "== 2. screensaver crash: watchdog clears flag, idle relaunches =="
srv arcadectl screensaver on >/dev/null
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1'
oldpid=$(cab pgrep -f "[a]rcade-screensaver")
died_before=$(cab sh -c 'grep -c "watchdog: screensaver died" /tmp/cabd.log || true')
cab pkill -9 -f "[a]rcade-screensaver" || true
# Detection proof: the watchdog must log the death+flag-clear. (The old
# assertion polled /status for a transient "screensaver": false — but the
# self-heal relaunch can complete inside one heartbeat, so the transient is
# not reliably observable; the log line is the non-racy witness.)
t=0; until [ "$(cab sh -c 'grep -c "watchdog: screensaver died" /tmp/cabd.log || true')" -gt "$died_before" ]; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: watchdog never detected the crash"; exit 1; }; sleep 0.5
done
echo "   crash detected, flag cleared"
# Heal proof: a NEW mpv (different pid) owns the screensaver window —
# idle=3 and no input since, so it must come back on its own.
t=0; until newpid=$(cab pgrep -f "[a]rcade-screensaver" 2>/dev/null) \
        && [ -n "$newpid" ] && [ "$newpid" != "$oldpid" ]; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: self-heal relaunch failed"; exit 1; }; sleep 0.5
done
# the new process is visible to pgrep the instant it spawns; its window is
# mapped a beat later, so wait for it rather than sampling once
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' \
    || { echo "FAIL: relaunched screensaver has no window"; exit 1; }
echo "   self-healed: screensaver relaunched (pid $oldpid -> $newpid)"

echo "== 3. disable --for suppresses idle relaunch; expiry restores =="
srv arcadectl screensaver disable --for 8s >/dev/null
ss_window && { echo "FAIL: disable did not stop screensaver"; exit 1; } || true
sleep 5   # idle=3 elapsed, but suppressed
ss_window && { echo "FAIL: screensaver started during suppression"; exit 1; } || true
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo post-suppression-relaunch-failed; exit 1'
echo "   suppression honored and expired correctly"

echo "== 3b. reachable server + ZERO enabled sources -> local clips, not silence =="
# The server answers fine and returns an empty playlist. This must take the
# clips fallback: previously it fell through silently (nothing started,
# nothing logged) because the except-branch fallback needs a FAILED fetch.
srv arcadectl screensaver off >/dev/null || true
srv arcadectl screensaver disable-source "$SA" >/dev/null
srv arcadectl --json screensaver playlist | grep -q '"playlist": \[\]' \
    || { echo "FAIL: playlist not empty after disabling every source"; exit 1; }
cab sh -c 'echo "BTN_A down" > /tmp/input-events'   # reset the idle clock
srv arcadectl screensaver set --idle 3 >/dev/null
cab sh -c 'for i in $(seq 1 60); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.5; done; echo empty-playlist-never-started; exit 1'
cab grep -q "empty playlist" /tmp/cabd.log || { echo "FAIL: empty-playlist path not logged"; exit 1; }
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver
p = screensaver.get_prop('path')
assert p and p.startswith('/var/cache/arcade-clips/'), f'not a local clip: {p}'
print('empty playlist -> local clip:', p)"
srv arcadectl screensaver off >/dev/null || true

echo "== 3c. forced ON with no sources also falls back (idle and button agree) =="
srv arcadectl screensaver on >/dev/null \
    || { echo "FAIL: forced on errored where idle falls back"; exit 1; }
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' \
    || { echo "FAIL: forced-on fallback produced no window"; exit 1; }
cab python3 -c "
import sys; sys.path.insert(0, '/cabinet')
import screensaver
p = screensaver.get_prop('path')
assert p and p.startswith('/var/cache/arcade-clips/'), f'not a local clip: {p}'
print('forced on -> local clip:', p)"
srv arcadectl screensaver off >/dev/null || true
# with NO sources and NO clips, it must still fail loudly (not silently)
cab sh -c 'mkdir -p /tmp/clip-stash && mv /var/cache/arcade-clips/*.ts /tmp/clip-stash/'
rc=0; srv arcadectl screensaver on >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: no sources + no clips exited $rc, want 4"; exit 1; }
cab sh -c 'mv /tmp/clip-stash/*.ts /var/cache/arcade-clips/'

echo "== 3d. fallback: off / dim_clock are honored by BOTH idle and forced =="
srv arcadectl screensaver off >/dev/null || true
t=0; until ! cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: screensaver would not stop"; exit 1; }; sleep 0.5
done
srv arcadectl screensaver set --fallback off >/dev/null
# a start decided before the policy change may still be in flight; let it
# land and stop it, so what we assert next is the NEW policy's behavior
sleep 3
srv arcadectl screensaver off >/dev/null || true
t=0; until ! cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1'; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: screensaver would not stop (pre-off)"; exit 1; }; sleep 0.5
done
rc=0; srv arcadectl screensaver on >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: forced-on with fallback=off exited $rc, want 4"; exit 1; }
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' \
    && { echo "FAIL: fallback=off still started a screensaver"; exit 1; } || true
cab sh -c 'echo "BTN_A down" > /tmp/input-events'
srv arcadectl screensaver set --idle 3 >/dev/null
sleep 6   # idle elapsed: with fallback=off nothing may appear
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' \
    && { echo "FAIL: idle started a screensaver with fallback=off"; exit 1; } || true
cab grep -q "fallback is 'off'" /tmp/cabd.log || { echo "FAIL: fallback=off not logged"; exit 1; }

srv arcadectl screensaver set --fallback dim_clock >/dev/null
srv arcadectl screensaver on >/dev/null \
    || { echo "FAIL: forced-on with fallback=dim_clock errored"; exit 1; }
cab sh -c 'for i in $(seq 1 40); do xdotool search --name arcade-screensaver >/dev/null 2>&1 && exit 0; sleep 0.25; done; exit 1' \
    || { echo "FAIL: dim_clock produced no window"; exit 1; }
cab pgrep -f "drawtext" >/dev/null || { echo "FAIL: dim_clock window is not the clock"; exit 1; }
srv arcadectl --json status | grep -q '"screensaver": true' \
    || { echo "FAIL: dim_clock did not set the screensaver flag"; exit 1; }
srv arcadectl screensaver off >/dev/null
srv arcadectl screensaver set --fallback local_clips >/dev/null

echo "== 3e. invalid mode/fallback values are rejected, not silently committed =="
# CLI rejects at parse time (exit 2, usage) ...
for bad in "--fallback dimclock" "--mode shuffle"; do
    rc=0; srv arcadectl screensaver set $bad >/dev/null 2>&1 || rc=$?
    [ "$rc" -eq 2 ] || { echo "FAIL: CLI took '$bad' (exit $rc, want 2 usage)"; exit 1; }
done
# ... and the REST API rejects it too, so no other client can commit a
# value nothing implements
srv python3 - <<'EOF'
import json, urllib.request, urllib.error
for bad in ({"fallback": "dimclock"}, {"mode": "shuffle"}, {"fallback": "on"}):
    req = urllib.request.Request("http://localhost:8000/screensaver/set",
                                 data=json.dumps(bad).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        raise SystemExit(f"FAIL: server accepted {bad}")
    except urllib.error.HTTPError as e:
        assert e.code == 400, f"{bad} -> {e.code}, want 400"
print("invalid mode/fallback rejected by CLI and API")
EOF
srv arcadectl --json screensaver status | grep -q '"fallback": "local_clips"' \
    || { echo "FAIL: a rejected value leaked into state"; exit 1; }

echo "== 3f. screensaver must never cover the lifeline screen =="
srv arcadectl cab lifeline on >/dev/null
rc=0; srv arcadectl screensaver on >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 4 ] || { echo "FAIL: forced screensaver over lifeline exited $rc, want 4"; exit 1; }
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' \
    && { echo "FAIL: screensaver started on top of lifeline"; exit 1; } || true
cab sh -c 'echo "BTN_A down" > /tmp/input-events'   # also resets idle clock
sleep 6   # idle=3 elapsed while lifeline is up
cab sh -c 'xdotool search --name arcade-screensaver >/dev/null 2>&1' \
    && { echo "FAIL: idle screensaver buried the lifeline screen"; exit 1; } || true
cab sh -c 'xdotool search --name arcade-lifeline >/dev/null' \
    || { echo "FAIL: lifeline window gone"; exit 1; }
srv arcadectl cab lifeline off >/dev/null

echo "== 3g. cab press a restarts networking while lifeline is up =="
cab rm -f /tmp/net-restarted
srv arcadectl cab lifeline on >/dev/null
srv arcadectl --json cab press a | grep -q '"via": "lifeline"' \
    || { echo "FAIL: press a not routed to the lifeline action"; exit 1; }
t=0; until cab test -e /tmp/net-restarted; do
    t=$((t+1)); [ $t -ge 20 ] && { echo "FAIL: press a did not restart networking"; exit 1; }; sleep 0.5
done
srv arcadectl cab lifeline off >/dev/null

srv arcadectl screensaver enable-source "$SA" >/dev/null
srv arcadectl screensaver set --idle 3600 >/dev/null

echo "== 4. cleanup =="
srv arcadectl screensaver set --idle 3600 >/dev/null
srv arcadectl screensaver off >/dev/null || true
sleep 1

echo "TEST-FALLBACK PASS"
