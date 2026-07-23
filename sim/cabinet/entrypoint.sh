#!/bin/sh
# Cabinet sim container: X server + session bus + PipeWire, then wait.
# cabd (M0) will be started here once it exists.
set -eu

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

Xvfb :0 -screen 0 1024x768x24 &

dbus-daemon --session --address="unix:path=$XDG_RUNTIME_DIR/bus" --fork
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"

pipewire &
pipewire-pulse &
wireplumber &

# cabd under a Restart=always-style supervisor (systemd analog, spec §5.1)
(while true; do
    python3 /cabinet/cabd.py >> /tmp/cabd.log 2>&1
    echo "cabd exited, respawning" >> /tmp/cabd.log
    sleep 0.5
done) &

exec sleep infinity
