#!/bin/sh
# arcaded entrypoint: seed a WRITABLE git state repo from the RO-mounted seed
# (SRV-2: every state change is a commit). Idempotent across restarts.
set -eu

if [ -d /state-seed ] && [ ! -d /statework/.git ]; then
    mkdir -p /statework
    cp -r /state-seed/. /statework/
    cd /statework
    git init -q
    git config user.email "arcaded@localhost"
    git config user.name "arcaded"
    git add -A
    git commit -qm "seed: initial state from repo"
fi
export STATE_DIR=/statework

exec python3 /app/arcaded.py
