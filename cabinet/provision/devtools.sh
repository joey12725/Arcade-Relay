#!/usr/bin/env bash
# Agentic-development toolchain for the cabinet.
#
# The cabinet is a real Debian box, so it can host the same agent workflow
# used to build it. Everything here is OPTIONAL relative to the arcade
# itself: install.sh calls this last, and a failure in any single tool is
# reported but does not fail cabinet provisioning — a broken Node install
# must never cost you the arcade.
#
# Run standalone to add/refresh tools on an already-provisioned cabinet:
#   sudo ./cabinet/provision/devtools.sh
set -uo pipefail

ARCADE_USER=${ARCADE_USER:-arcade}
NODE_MAJOR=${NODE_MAJOR:-24}
HOME_DIR=$(getent passwd "$ARCADE_USER" | cut -d: -f6)
FAILED=()

step() { echo; echo "== $* =="; }
note_fail() { FAILED+=("$1"); echo "!! $1 FAILED (continuing)"; }
as_user() { runuser -u "$ARCADE_USER" -- "$@"; }

step "base development tools"
apt-get update
apt-get install -y --no-install-recommends \
    git git-lfs build-essential pkg-config \
    curl wget ca-certificates gnupg \
    ripgrep jq unzip zip \
    tmux vim nano less openssh-server \
    python3-pip python3-venv \
  || note_fail "base dev tools"

# SSH is how you reach the cabinet for agentic work (and lifeline's recovery
# path assumes it exists)
systemctl enable --now ssh 2>/dev/null || systemctl enable --now sshd 2>/dev/null || true

step "Node.js ${NODE_MAJOR} (NodeSource)"
if command -v node >/dev/null && node -v | grep -q "^v${NODE_MAJOR}\."; then
    echo "node $(node -v) already installed"
else
    if curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" -o /tmp/nodesource.sh; then
        if bash /tmp/nodesource.sh; then
            apt-get install -y nodejs || note_fail "nodejs (apt install)"
        else
            note_fail "nodejs (NodeSource repo setup)"
        fi
    else
        note_fail "nodejs (NodeSource unreachable)"
    fi
fi

# npm global installs go to the arcade user's own prefix: no sudo needed for
# later updates, and agent CLIs land on the user's PATH
step "npm prefix for ${ARCADE_USER}"
if command -v npm >/dev/null; then
    install -d -o "$ARCADE_USER" -g "$ARCADE_USER" "$HOME_DIR/.npm-global"
    as_user npm config set prefix "$HOME_DIR/.npm-global" || note_fail "npm prefix"
    if ! grep -q ".npm-global/bin" "$HOME_DIR/.profile" 2>/dev/null; then
        # shellcheck disable=SC2016  # $HOME must stay literal in .profile
        echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$HOME_DIR/.profile"
        chown "$ARCADE_USER:$ARCADE_USER" "$HOME_DIR/.profile"
    fi
fi

# Agent CLIs. Package names are data so this list is trivial to amend —
# add a line, re-run this script.
step "agent CLIs"
NPM_TOOLS=(
    "@anthropic-ai/claude-code"     # Claude Code
    "@openai/codex"                 # Codex CLI
)
for pkg in "${NPM_TOOLS[@]}"; do
    if command -v npm >/dev/null; then
        echo "-- $pkg"
        as_user env PATH="$HOME_DIR/.npm-global/bin:$PATH" \
            npm install -g "$pkg" || note_fail "$pkg"
    else
        note_fail "$pkg (no npm)"
    fi
done

step "Tailscale"
if command -v tailscale >/dev/null; then
    echo "tailscale already installed"
elif curl -fsSL https://tailscale.com/install.sh -o /tmp/tailscale.sh; then
    sh /tmp/tailscale.sh || note_fail "tailscale"
else
    note_fail "tailscale (installer unreachable)"
fi
# NB: `tailscale up` is interactive (browser auth) — do it once via remote
# view, then set LIFELINE_TRANSPORT_CMD="tailscale up" in cabd.service so
# lifeline can bring the fallback transport up by itself.

step "summary"
for t in git node npm claude codex tailscale rg jq tmux; do
    if command -v "$t" >/dev/null 2>&1 || \
       [ -x "$HOME_DIR/.npm-global/bin/$t" ]; then
        v=$({ command -v "$t" >/dev/null 2>&1 && "$t" --version 2>/dev/null; } | head -1)
        printf '  %-12s %s\n' "$t" "${v:-installed}"
    else
        printf '  %-12s MISSING\n' "$t"
    fi
done

if [ ${#FAILED[@]} -gt 0 ]; then
    echo
    echo "devtools finished with ${#FAILED[@]} failure(s): ${FAILED[*]}"
    echo "the arcade itself is unaffected; re-run this script to retry"
    exit 0   # never fail cabinet provisioning over an optional tool
fi
echo
echo "devtools OK"
