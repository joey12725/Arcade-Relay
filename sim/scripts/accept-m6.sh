#!/usr/bin/env bash
# M6 acceptance (spec §8): "a fresh agent given only the repo + 'make the
# screensaver dimmer and disable Redline' completes it via CLI without human
# help."
#
# The agent step cannot run inside bash, so this script has two phases and
# the harness (the orchestrating agent loop) spawns a COLD subagent between
# them, giving it ONLY the repo path and the task sentence:
#   accept-m6.sh setup   -> arranges known pre-state, prints the baseline
#   <cold agent does the task using only repo docs + arcadectl>
#   accept-m6.sh check   -> exits 0 iff brightness dropped AND Redline is
#                           disabled AND both changes are git commits
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

phase="${1:-}"

if [ "$phase" = "setup" ]; then
    compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet jellyfin toxiproxy
    if ! tp inspect cab_link >/dev/null 2>&1; then
        tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
    fi
    tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
    t=0; until srv arcadectl --json status | grep -q '"online": true'; do
        t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
    done
    t=0; until srv arcadectl screensaver sources 2>/dev/null | grep -q "Redline"; do
        t=$((t+1)); [ $t -ge 120 ] && { echo "FAIL: library never scanned"; exit 1; }; sleep 2
    done
    RL=$(srv arcadectl screensaver sources | python3 -c 'import sys,json; print(next(i["id"] for i in json.load(sys.stdin) if i["name"].startswith("Redline")))')
    srv arcadectl screensaver enable-source "$RL" >/dev/null
    srv arcadectl screensaver set --brightness 80 >/dev/null
    echo "SETUP-OK baseline: brightness=80, Redline enabled (id $RL)"
    exit 0
fi

if [ "$phase" = "check" ]; then
    B=$(srv arcadectl --json screensaver status | python3 -c 'import sys,json; print(json.load(sys.stdin)["brightness"])')
    [ "$B" -lt 80 ] || { echo "FAIL: brightness is $B, not dimmer than 80"; exit 1; }
    srv arcadectl screensaver sources | python3 -c '
import sys, json
rl = next(i for i in json.load(sys.stdin) if i["name"].startswith("Redline"))
assert not rl["enabled"], "Redline still enabled"' || { echo "FAIL: Redline enabled"; exit 1; }
    srv arcadectl state log | grep -q "disable-source" || { echo "FAIL: no disable commit"; exit 1; }
    srv arcadectl state log | grep -qE "brightness=" || { echo "FAIL: no brightness commit"; exit 1; }
    echo "CHECK-OK: brightness=$B (<80), Redline disabled, both git-committed"
    echo "ACCEPT-M6 PASS"
    exit 0
fi

echo "usage: accept-m6.sh setup|check  (cold agent runs between phases)" >&2
exit 2
