FROM debian:trixie-slim

# ffmpeg: arcaded does its own 480p/1.5Mbps transcode for the screensaver.
# Plex's own transcoder is not usable here — it downloads codecs from
# codecs.plex.tv on first use, which an isolated server cannot reach, and it
# fails at the sub-playlist with a 500 rather than degrading. Owning the
# transcode also means the tunnel budget is actually enforced by us.
# python3-paho-mqtt: the Home Assistant integration. Optional at runtime —
# arcaded logs and carries on without it — but shipped so enabling the
# integration in the web UI just works.
# openssh-client is only a Recommends of git, so --no-install-recommends drops
# it; the auto-updating deploy entrypoint pulls over SSH (read-only deploy key)
# and needs it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-aiohttp python3-yaml git openssh-client ffmpeg \
        python3-venv python3-pip python3-paho-mqtt nodejs \
    && rm -rf /var/lib/apt/lists/*

# Keep YouTube extraction current, including the EJS challenge solver.
RUN python3 -m venv /opt/ytdlp \
    && /opt/ytdlp/bin/pip install --no-cache-dir 'yt-dlp[default]' \
    && ln -s /opt/ytdlp/bin/yt-dlp /usr/local/bin/yt-dlp

# NB: the build context is the REPO ROOT, not server/ — the Y2K asset pack
# below lives at the top level because the Pegasus theme uses it too. Build
# with `docker build -f server/Dockerfile .`; sim/docker-compose.yml does.
# Every server module (arcaded imports all of them) plus the web UI.
COPY server/*.py server/webui.html /app/
# Skins, sprites and the tagged GIF library, served at /assets for the web UI.
COPY assets /app/assets
COPY server/arcadectl /usr/local/bin/arcadectl
COPY server/entrypoint.sh /entrypoint.sh
# Alternate entrypoint for deployments that run from a bind-mounted git
# checkout and self-update (see server/provision/docker-entrypoint.sh). Baked
# in only as a fallback -- the deploy overrides `entrypoint:` to the copy in
# the checkout, so the updater updates itself too.
COPY server/provision/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /usr/local/bin/arcadectl /entrypoint.sh /docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
