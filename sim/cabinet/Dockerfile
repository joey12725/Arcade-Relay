FROM debian:trixie-slim

# Real Xvfb, real mpv, real PipeWire stack (docs/assumptions.md A-8: only game/streaming
# binaries get faked, the AV/window plumbing is real).
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11-utils xdotool wmctrl imagemagick x11vnc \
        retroarch libretro-gambatte libretro-core-info \
        libgl1 libglx-mesa0 libgl1-mesa-dri \
        mpv \
        pipewire pipewire-pulse wireplumber dbus pulseaudio-utils \
        python3 python3-evdev python3-aiohttp python3-pil \
        curl ca-certificates procps imagemagick \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV DISPLAY=:0 \
    LIBGL_ALWAYS_SOFTWARE=1 \
    XDG_RUNTIME_DIR=/tmp/xdg

ENTRYPOINT ["/entrypoint.sh"]
