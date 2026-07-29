FROM debian:trixie-slim

# ffmpeg: arcaded does its own 480p/1.5Mbps transcode for the screensaver.
# Plex's own transcoder is not usable here — it downloads codecs from
# codecs.plex.tv on first use, which an isolated server cannot reach, and it
# fails at the sub-playlist with a 500 rather than degrading. Owning the
# transcode also means the tunnel budget is actually enforced by us.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-aiohttp python3-yaml git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY arcaded.py /app/arcaded.py
COPY plex.py /app/plex.py
COPY shuffle.py /app/shuffle.py
COPY romdl.py /app/romdl.py
COPY webui.html /app/webui.html
COPY arcadectl /usr/local/bin/arcadectl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/arcadectl /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
