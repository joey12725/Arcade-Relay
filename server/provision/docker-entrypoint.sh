#!/bin/sh
# Auto-updating container entrypoint (used by the self-hosted Compose deploy).
#
# Difference from server/entrypoint.sh: that one runs the code baked into the
# image at /app, so a code change needs an image rebuild. This one runs arcaded
# straight out of a git checkout bind-mounted at $ARCADE_SRC and polls the
# remote every $ARCADE_UPDATE_INTERVAL seconds. A new commit on $ARCADE_BRANCH
# is fast-forwarded in and the container exits so its restart policy brings it
# back on the new code -- no image rebuild, no talking to the docker daemon.
#
# Only dependency changes (server/Dockerfile) still need a manual rebuild; we
# log loudly when one lands so it is not a silent no-op.
#
# arcaded resolves webui.html and assets/ relative to its own __file__, so
# running from the checkout serves the checkout's UI and asset pack with no
# copy step.
set -eu

SRC=${ARCADE_SRC:-/srv/arcade}
BRANCH=${ARCADE_BRANCH:-main}
INTERVAL=${ARCADE_UPDATE_INTERVAL:-120}

log() { echo "[deploy] $*"; }

# The checkout is a bind mount owned by the host, which git otherwise refuses
# to operate on ("dubious ownership").
git config --global --add safe.directory "$SRC"

# --- writable state repo (SRV-2: every state change is a commit) ------------
STATE_DIR=${STATE_DIR:-/statework}
export STATE_DIR
export ROMDL_STAGING=${ROMDL_STAGING:-/arcade-media/incoming}
# ROM_SOURCE_DIR (your own <system>/<file> game library) passes straight through.
if [ ! -d "$STATE_DIR/.git" ]; then
    log "seeding state repo at $STATE_DIR from $SRC/state"
    mkdir -p "$STATE_DIR"
    cp -r "$SRC"/state/. "$STATE_DIR"/
    git -C "$STATE_DIR" init -q
    git -C "$STATE_DIR" config user.email "arcaded@localhost"
    git -C "$STATE_DIR" config user.name "arcaded"
    git -C "$STATE_DIR" add -A
    git -C "$STATE_DIR" commit -qm "seed: initial state from repo"
fi

head_sha()   { git -C "$SRC" rev-parse HEAD; }
remote_sha() { git -C "$SRC" rev-parse "origin/$BRANCH"; }
docker_sha() { git -C "$SRC" rev-parse "HEAD:server/Dockerfile" 2>/dev/null || echo none; }

# Fast-forward to origin/$BRANCH. Deliberately --ff-only, never reset --hard:
# if someone has hand-edited the checkout we stop and say so rather than
# throwing their work away.
update() {
    git -C "$SRC" fetch --quiet --prune origin "$BRANCH" || { log "fetch failed"; return 1; }
    before=$(head_sha)
    [ "$before" = "$(remote_sha)" ] && return 1
    before_df=$(docker_sha)
    if ! git -C "$SRC" merge --ff-only --quiet "origin/$BRANCH"; then
        log "WARNING: $SRC cannot fast-forward to origin/$BRANCH (local edits?); staying on $before"
        return 1
    fi
    log "updated $before -> $(head_sha)"
    if [ "$before_df" != "$(docker_sha)" ]; then
        log "WARNING: server/Dockerfile changed -- rebuild the image for dependency changes"
    fi
    return 0
}

# Catch up before serving, so a restart for any reason lands on current code.
update || true

# The image bakes arcadectl in at build time, so without this the CLI inside
# the container lags the checkout until the next image rebuild.
install -m 0755 "$SRC/server/arcadectl" /usr/local/bin/arcadectl \
    || log "WARNING: could not refresh /usr/local/bin/arcadectl from the checkout"

if [ "${ARCADE_AUTO_UPDATE:-1}" != "0" ]; then
    log "watching origin/$BRANCH every ${INTERVAL}s"
    (
        while true; do
            sleep "$INTERVAL"
            # PID 1 is arcaded (exec'd below). TERM it for a graceful aiohttp
            # shutdown; the container's restart policy re-runs this script,
            # which picks up the freshly fast-forwarded code.
            update && { log "restarting onto $(head_sha)"; kill 1; }
        done
    ) &
fi

log "arcaded starting from $(head_sha) on $BRANCH"
exec python3 "$SRC/server/arcaded.py"
