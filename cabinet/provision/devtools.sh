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
# T3 release the service runs. Keep it in step with the desktop app that
# connects to it, so the version is pinned in t3-version and arcade-update.sh
# re-pins + restarts the service whenever that file changes. (If the app says
# "not connected" while the relay is healthy, check the clock first: see the
# timesyncd note in install.sh.)
T3_PIN=$(tr -d '[:space:]' < "$(dirname "$0")/t3-version" 2>/dev/null)
T3_PKG=${T3_PKG:-t3@${T3_PIN:-latest}}
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
    bubblewrap \
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
        echo 'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"' \
            >> "$HOME_DIR/.profile"
        chown "$ARCADE_USER:$ARCADE_USER" "$HOME_DIR/.profile"
    fi
fi

# T3 Code requires node ^22.16, ^23.11, or >=24.10 (docs/t3-setup.md §3).
# Assert it rather than assume: a too-old node fails at T3 pairing time,
# which is a miserable place to discover it.
if command -v node >/dev/null; then
    NODE_V=$(node -v | sed 's/^v//')
    # oldest-first sort: if 24.10.0 sorts first, node is >= the minimum
    OLDEST=$(printf '%s\n24.10.0\n' "$NODE_V" | sort -V | head -1)
    if [ "$OLDEST" = "24.10.0" ]; then
        echo "node $NODE_V OK for T3 (needs >=24.10)"
    else
        note_fail "node $NODE_V is below T3's 24.10 minimum"
    fi
fi

step "Claude Code (native Linux build)"
# The native installer is the current supported route (docs/t3-setup.md §4);
# the npm package is not the same build.
if [ -x "$HOME_DIR/.local/bin/claude" ] || command -v claude >/dev/null; then
    echo "claude already installed"
elif curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh; then
    as_user bash /tmp/claude-install.sh || note_fail "claude code"
else
    note_fail "claude code (installer unreachable)"
fi

step "Codex CLI"
if [ -x "$HOME_DIR/.npm-global/bin/codex" ] || command -v codex >/dev/null; then
    echo "codex already installed"
elif command -v npm >/dev/null; then
    as_user env PATH="$HOME_DIR/.npm-global/bin:$PATH" \
        npm install -g @openai/codex || note_fail "codex"
else
    note_fail "codex (no npm)"
fi

step "T3 Code (remote dev server)"
# T3 is run via `npx t3@<pin>` rather than installed; the pin comes from
# t3-version (see T3_PKG above), so updating T3 means committing a new
# version there. We install the systemd USER unit here.
#
# Remote access is T3 Connect, not Tailscale Serve: T3 Connect carries its
# own relay, so the cabinet needs no tailnet membership and no open port to
# be reachable from the laptop or a phone. The one interactive step is
#   runuser -u arcade -- npx --yes t3@latest connect link --headless
# which prints an out-of-band OAuth URL to open on any device; the grant is
# persisted under the arcade user's T3 data dir, so it survives reboots and
# the service picks it up on next start.
if command -v npx >/dev/null; then
    install -d -o "$ARCADE_USER" -g "$ARCADE_USER" \
        "$HOME_DIR/.config/systemd/user" "$HOME_DIR/dev"
    NPX_PATH=$(command -v npx)
    cat > "$HOME_DIR/.config/systemd/user/t3-code.service" <<EOF
[Unit]
Description=T3 Code remote development server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/dev
ExecStart=${NPX_PATH} --yes ${T3_PKG} serve --no-browser
Restart=always
RestartSec=5
Environment=PATH=%h/.local/bin:%h/.npm-global/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF
    chown "$ARCADE_USER:$ARCADE_USER" \
        "$HOME_DIR/.config/systemd/user/t3-code.service"
    # enable without starting: T3 needs `connect link` run once first, and a
    # crash-looping unit before that is just noise
    install -d -o "$ARCADE_USER" -g "$ARCADE_USER" \
        "$HOME_DIR/.config/systemd/user/default.target.wants"
    ln -sf ../t3-code.service \
        "$HOME_DIR/.config/systemd/user/default.target.wants/t3-code.service"
    chown -h "$ARCADE_USER:$ARCADE_USER" \
        "$HOME_DIR/.config/systemd/user/default.target.wants/t3-code.service"
    echo "t3-code.service installed (${T3_PKG}; starts once T3 Connect is linked)"
else
    note_fail "t3-code service (no npx)"
fi

step "keep the cabinet awake (T3 needs it reachable)"
# docs/t3-setup.md §12 — the DISPLAY may blank; the machine must not sleep.
systemctl mask sleep.target suspend.target hibernate.target \
    hybrid-sleep.target >/dev/null 2>&1 \
    && echo "sleep/suspend/hibernate masked" \
    || echo "could not mask sleep targets (no systemd here?)"

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
# T3 also needs the arcade user to hold tailscale operator rights:
#   sudo tailscale set --operator=arcade
if command -v tailscale >/dev/null; then
    tailscale set --operator="$ARCADE_USER" >/dev/null 2>&1 \
        && echo "tailscale operator = $ARCADE_USER" \
        || echo "set tailscale operator after 'tailscale up'"
fi

# linger so the T3 user service (and cabd) run with nobody logged in
loginctl enable-linger "$ARCADE_USER" >/dev/null 2>&1 || true

step "summary"
# Tools land in three places: system PATH, the user's npm prefix, and
# ~/.local/bin (the native Claude installer). Check all three, and treat a
# tool that is missing after its install step as a failure.
find_tool() {
    for c in "$HOME_DIR/.local/bin/$1" "$HOME_DIR/.npm-global/bin/$1"; do
        [ -x "$c" ] && { echo "$c"; return 0; }
    done
    command -v "$1" 2>/dev/null
}
for t in git node npm npx claude codex tailscale rg jq tmux; do
    bin=$(find_tool "$t")
    if [ -n "$bin" ]; then
        v=$("$bin" --version 2>/dev/null | head -1)
        printf '  %-12s %s\n' "$t" "${v:-installed}"
    else
        printf '  %-12s MISSING\n' "$t"
        case "$t" in
            claude|codex|node|npx|tailscale)
                note_fail "$t missing after install" ;;
        esac
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
