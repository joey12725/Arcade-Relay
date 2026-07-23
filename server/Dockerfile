FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-aiohttp \
    && rm -rf /var/lib/apt/lists/*

COPY arcaded.py /app/arcaded.py
COPY arcadectl /usr/local/bin/arcadectl
RUN chmod +x /usr/local/bin/arcadectl

EXPOSE 8000
CMD ["python3", "/app/arcaded.py"]
