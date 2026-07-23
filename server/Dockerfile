FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-aiohttp python3-yaml git \
    && rm -rf /var/lib/apt/lists/*

COPY arcaded.py /app/arcaded.py
COPY jellyfin.py /app/jellyfin.py
COPY romdl.py /app/romdl.py
COPY webui.html /app/webui.html
COPY arcadectl /usr/local/bin/arcadectl
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/arcadectl /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
