#!/bin/sh
# Chaos driver: forever, at random 5-20s intervals, either
#   - partition: disable the toxiproxy link for 2-8s, then re-enable
#   - kill: kill -9 the heartbeat process inside the cabinet container
# Every action is logged with epoch-ms timestamps so soak-status can
# distinguish chaos-caused failures from genuine violations.
#
# NB: runs on busybox (docker:27-cli) — no GNU date (%N unsupported), so
# timestamps are second-precision * 1000 and rand() is seeded from a counter.
set -u
TP="docker exec soak-toxiproxy /toxiproxy-cli"
LOG=/soak/chaos.log
: > "$LOG"

now_ms() { echo $(( $(date +%s) * 1000 )); }

# Ensure the proxy exists (idempotent across restarts)
until $TP inspect cab_link >/dev/null 2>&1; do
    $TP create --listen 0.0.0.0:8666 --upstream server:8000 cab_link >/dev/null 2>&1 || sleep 1
done
echo "$(now_ms) READY proxy cab_link up" >> "$LOG"

CNT=0
rand() { # rand MIN MAX -> integer in [MIN,MAX]
    CNT=$((CNT + 1))
    # Discard the first draw: on busybox awk, first-rand values of nearby
    # seeds are correlated — two calls in the same second used to produce a
    # locked "coin" (observed: 0 heartbeat-kills in 245 flips).
    awk -v min="$1" -v max="$2" \
        -v seed="$(( $(date +%s) * 131 + CNT * 104729 + $$ ))" \
        'BEGIN{srand(seed); rand(); print int(min + rand()*(max-min+1))}'
}

while true; do
    sleep "$(rand 5 20)"
    act=$(rand 0 3)   # one draw picks the action: no correlated nesting
    if [ "$act" -le 1 ]; then
        dur=$(rand 2 8)
        echo "$(now_ms) PARTITION-START ${dur}s" >> "$LOG"
        $TP toggle cab_link >/dev/null 2>&1
        sleep "$dur"
        $TP toggle cab_link >/dev/null 2>&1
        echo "$(now_ms) PARTITION-END" >> "$LOG"
    elif [ "$act" -eq 2 ]; then
        echo "$(now_ms) KILL9 heartbeat" >> "$LOG"
        docker exec soak-cabinet pkill -9 -f heartbeat.sh >/dev/null 2>&1
    else
        echo "$(now_ms) KILL9 cabd" >> "$LOG"
        docker exec soak-cabinet pkill -9 -f cabd.py >/dev/null 2>&1
    fi
done
