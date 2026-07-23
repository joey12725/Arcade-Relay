#!/bin/sh
# Soak-mode cabinet entrypoint: supervised accelerated heartbeat.
# The supervisor loop is the stand-in for systemd Restart=always: when chaos
# kill -9s the heartbeat, it must come back within the supervision interval.
set -u
mkdir -p /soak
: > /soak/heartbeat.log

# Real cabd under its own Restart=always supervisor; chaos kill -9s it and
# it must reconnect (jittered backoff) fast enough that arcaded's view
# (sampled by poller.py) never shows OFFLINE outside the grace window.
(while true; do
    python3 /cabinet/cabd.py >> /soak/cabd.log 2>&1
    sleep 0.2
done) &

while true; do
    sh /soak-bin/heartbeat.sh >> /soak/heartbeat.log 2>/dev/null
    # heartbeat.sh only exits if killed; note the restart, then respawn
    echo "$(date +%s%3N) SUPERVISOR-RESTART" >> /soak/heartbeat.log
    sleep 0.2
done
