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
        python3-venv python3-pip python3-paho-mqtt \
    && rm -rf /var/lib/apt/lists/*

# ROMDL (example.invalid/romdl) in its own venv, exposed only as a CLI.
# Deliberately NOT installed into the system interpreter: arcaded talks to it
# over the documented `--json` interface, so its dependency tree (requests,
# bs4, rapidfuzz, tqdm) never has to be importable from the daemon.
# Build-arg pinned so an image rebuild is reproducible; bump to update.
ARG ROMDL_REF=main
RUN python3 -m venv /opt/romdl \
    && /opt/romdl/bin/pip install --no-cache-dir \
         "git+https://example.invalid/romdl@${ROMDL_REF}" \
    && ln -s /opt/romdl/bin/romdl /usr/local/bin/romdl \
    && romdl --help >/dev/null

# NB: the build context is the REPO ROOT, not server/ — the Y2K asset pack
# below lives at the top level because the Pegasus theme uses it too. Build
# with `docker build -f server/Dockerfile .`; sim/docker-compose.yml does.
COPY server/arcaded.py /app/arcaded.py
COPY server/artdb.py /app/artdb.py
COPY server/plex.py /app/plex.py
COPY server/shuffle.py /app/shuffle.py
COPY server/romdl.py /app/romdl.py
COPY server/webui.html /app/webui.html
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
