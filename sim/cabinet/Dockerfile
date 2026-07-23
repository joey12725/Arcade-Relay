FROM debian:trixie-slim

# Real Xvfb, real mpv, real PipeWire stack (ASSUMPTIONS A-8: only game/streaming
# binaries get faked, the AV/window plumbing is real).
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11-utils xdotool wmctrl \
        mpv \
        pipewire pipewire-pulse wireplumber dbus \
        python3 python3-evdev python3-aiohttp \
        curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DISPLAY=:0 \
    XDG_RUNTIME_DIR=/tmp/xdg

ENTRYPOINT ["/entrypoint.sh"]
