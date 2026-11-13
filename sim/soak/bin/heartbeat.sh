#!/bin/sh
# Accelerated heartbeat: 4 Hz (vs the spec's 5s — ~20x, docs/assumptions.md A-7).
# One line per attempt: <epoch_ms> OK|FAIL
set -u
while true; do
    if curl -fsS -o /dev/null --max-time 1 http://toxiproxy:8666/; then
        echo "$(date +%s%3N) OK"
    else
        echo "$(date +%s%3N) FAIL"
    fi
    sleep 0.25
done
