#!/usr/bin/env bash
# Pull the latest cabinet code from GitHub and redeploy it.
#
# Driven by arcade-update.timer. The checkout at /opt/arcade/repo is the
# cabinet's copy of this repository; install.sh is re-run from it, which is
# what makes this script self-updating (a new version of *this file* lands
# with the same pull).
#
# Deliberately conservative:
#   - a fetch failure is not an error (the cabinet is often offline)
#   - nothing happens at all when the tree is already up to date
#   - an in-progress game defers the update to the next tick; the arcade is
#     never interrupted to install code
set -uo pipefail

REPO_DIR=${REPO_DIR:-/opt/arcade/repo}
ARCADE_USER=${ARCADE_USER:-arcade}
BRANCH=${ARCADE_UPDATE_BRANCH:-}
# Deploy key (read-only) — the repository is private.
KEY=${ARCADE_DEPLOY_KEY:-/etc/arcade/deploy_key}

log() { echo "[arcade-update] $*"; }

[ -d "$REPO_DIR/.git" ] || { log "no checkout at $REPO_DIR — nothing to do"; exit 0; }

if [ -r "$KEY" ]; then
    export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

cd "$REPO_DIR"
# System config, not --global: this runs as a systemd system service, where
# HOME is unset and a --global write goes nowhere. The checkout is owned by
# the arcade user while this runs as root, so without the exception every
# git call dies on "dubious ownership".
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$REPO_DIR" \
    || git config --system --add safe.directory "$REPO_DIR" || true

[ -n "$BRANCH" ] || BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ -n "$BRANCH" ] && [ "$BRANCH" != HEAD ] || {
    log "detached HEAD and no ARCADE_UPDATE_BRANCH set — refusing to guess"
    exit 0
}

if ! git fetch --prune --quiet origin "$BRANCH"; then
    log "fetch failed (offline?) — will retry next tick"
    exit 0
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse FETCH_HEAD)
if [ "$LOCAL" = "$REMOTE" ]; then
    log "already at ${LOCAL:0:8} — nothing to do"
    exit 0
fi

# Never yank the code out from under someone who is playing. cabd tracks the
# session in-process, so match on the session binaries cabd.service names.
if pgrep -u "$ARCADE_USER" -x retroarch >/dev/null 2>&1 \
   || pgrep -u "$ARCADE_USER" -f 'moonlight-qt|greenlight|steam .*rungameid' >/dev/null 2>&1; then
    log "a game is running — deferring ${REMOTE:0:8} to the next tick"
    exit 0
fi

log "updating ${LOCAL:0:8} -> ${REMOTE:0:8} on $BRANCH"
git reset --hard --quiet "$REMOTE" || { log "reset failed"; exit 1; }

# SERVER_HOST is required by install.sh. It was baked at first provision and
# is recorded here so unattended re-provisioning does not need it passed in.
# shellcheck disable=SC1091
[ -r /etc/arcade/provision.env ] && . /etc/arcade/provision.env

if [ -z "${SERVER_HOST:-}" ]; then
    log "no SERVER_HOST in /etc/arcade/provision.env — code updated, skipping re-provision"
    exit 0
fi

# Devtools are skipped by default: they reach out to NodeSource/Anthropic/
# OpenAI/Tailscale on every run, which is a lot of network for a routine code
# update. Set ARCADE_UPDATE_DEVTOOLS=1 to refresh them too.
if [ "${ARCADE_UPDATE_DEVTOOLS:-0}" = 1 ]; then SKIP_DEVTOOLS=0; else SKIP_DEVTOOLS=1; fi

if SERVER_HOST="$SERVER_HOST" ARCADE_USER="$ARCADE_USER" SKIP_DEVTOOLS="$SKIP_DEVTOOLS" \
   bash "$REPO_DIR/cabinet/provision/install.sh"; then
    log "re-provisioned at ${REMOTE:0:8}"
else
    log "install.sh failed — code is at ${REMOTE:0:8} but the deploy is incomplete"
    exit 1
fi

# install.sh refreshes the unit files; the running daemons keep executing the
# old code until they are restarted (DEPLOY.md §8).
UID_N=$(id -u "$ARCADE_USER")
runuser -u "$ARCADE_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_N" \
    systemctl --user daemon-reload 2>/dev/null || true
runuser -u "$ARCADE_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_N" \
    systemctl --user restart cabd.service pegasus.service 2>/dev/null \
    && log "cabd + pegasus restarted" \
    || log "could not restart user units (no session?) — restart at next boot"

# T3 rides along without the rest of devtools: when t3-version moved, re-pin
# the existing unit and restart it. Only a commit to t3-version does this, so
# a restart (which drops any live agent session) is always deliberate.
T3_UNIT="$(getent passwd "$ARCADE_USER" | cut -d: -f6)/.config/systemd/user/t3-code.service"
T3_PIN=$(tr -d '[:space:]' < "$REPO_DIR/cabinet/provision/t3-version" 2>/dev/null)
if [ -f "$T3_UNIT" ] && [ -n "$T3_PIN" ] \
   && ! grep -q -- "--yes t3@${T3_PIN} serve" "$T3_UNIT"; then
    sed -i -E "s#--yes t3@[^ ]+ serve#--yes t3@${T3_PIN} serve#" "$T3_UNIT"
    runuser -u "$ARCADE_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_N" \
        systemctl --user daemon-reload 2>/dev/null || true
    runuser -u "$ARCADE_USER" -- env XDG_RUNTIME_DIR="/run/user/$UID_N" \
        systemctl --user restart t3-code.service 2>/dev/null \
        && log "t3-code re-pinned to ${T3_PIN} and restarted" \
        || log "t3-code re-pinned to ${T3_PIN}; restart it by hand"
fi

log "done"
