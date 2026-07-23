#!/bin/sh
# Soak-mode cabinet entrypoint: supervised accelerated heartbeat.
# The supervisor loop is the stand-in for systemd Restart=always: when chaos
# kill -9s the heartbeat, it must come back within the supervision interval.
set -u
mkdir -p /soak
: > /soak/heartbeat.log

while true; do
    sh /soak-bin/heartbeat.sh >> /soak/heartbeat.log 2>/dev/null
    # heartbeat.sh only exits if killed; note the restart, then respawn
    echo "$(date +%s%3N) SUPERVISOR-RESTART" >> /soak/heartbeat.log
    sleep 0.2
done
