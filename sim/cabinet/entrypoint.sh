#!/bin/sh
# Cabinet sim container: X server + session bus + PipeWire, then wait.
# cabd (M0) will be started here once it exists.
set -eu

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# container *restart* keeps the fs: clear stale X locks or Xvfb won't start
rm -f /tmp/.X0-lock /tmp/.X11-unix/X0

Xvfb :0 -screen 0 1024x768x24 &

dbus-daemon --session --address="unix:path=$XDG_RUNTIME_DIR/bus" --fork
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

# Audio stack under Restart=always supervisors: at container start these
# race each other, and a wireplumber that loses (pipewire socket not up yet)
# can die — leaving PipeWire with no session manager, where volume/mute sets
# return success but never apply. Supervision makes the race harmless.
(while true; do pipewire >>/tmp/pipewire.log 2>&1 || true; sleep 0.5; done) &
(while true; do pipewire-pulse >>/tmp/pipewire-pulse.log 2>&1 || true; sleep 0.5; done) &
(while true; do wireplumber >>/tmp/wireplumber.log 2>&1 || true; sleep 0.5; done) &

# containers have no audio hardware: load a null sink so real audio nodes
# exist for the policy engine (retry until pipewire-pulse is up). A silent
# anchor stream keeps the sink RUNNING — a suspended null sink silently
# discards volume changes (sim-only quirk; real ALSA sinks store volume).
# The anchor is supervised too: at boot its pulse connection can fail.
(i=0; until pactl load-module module-null-sink sink_name=simsink >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -ge 40 ] && break; sleep 0.25
done
while true; do
    env 'PULSE_PROP_media.role=system' mpv --no-config --no-video --ao=pulse \
        --loop=inf --really-quiet "av://lavfi:anullsrc=r=48000" >/dev/null 2>&1 || true
    sleep 0.5
done) &

# cabd under a Restart=always-style supervisor (systemd analog, spec §5.1).
# `|| true` is load-bearing: set -e is inherited by the subshell, and without
# it a non-zero cabd exit (any crash/kill) kills the supervisor loop itself.
(while true; do
    python3 /cabinet/cabd.py >> /tmp/cabd.log 2>&1 || true
    echo "cabd exited, respawning" >> /tmp/cabd.log
    sleep 0.5
done) &

exec sleep infinity
