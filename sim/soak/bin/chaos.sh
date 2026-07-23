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
    awk -v min="$1" -v max="$2" -v seed="$(( $(date +%s) + CNT * 7919 + $$ ))" \
        'BEGIN{srand(seed); print int(min + rand()*(max-min+1))}'
}

while true; do
    sleep "$(rand 5 20)"
    if [ "$(rand 0 1)" -eq 0 ]; then
        dur=$(rand 2 8)
        echo "$(now_ms) PARTITION-START ${dur}s" >> "$LOG"
        $TP toggle cab_link >/dev/null 2>&1
        sleep "$dur"
        $TP toggle cab_link >/dev/null 2>&1
        echo "$(now_ms) PARTITION-END" >> "$LOG"
    else
        if [ "$(rand 0 1)" -eq 0 ]; then
            echo "$(now_ms) KILL9 heartbeat" >> "$LOG"
            docker exec soak-cabinet pkill -9 -f heartbeat.sh >/dev/null 2>&1
        else
            echo "$(now_ms) KILL9 cabd" >> "$LOG"
            docker exec soak-cabinet pkill -9 -f cabd.py >/dev/null 2>&1
        fi
    fi
done
