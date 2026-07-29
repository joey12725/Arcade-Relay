# Arcade Cabinet Remote Development Setup

This guide configures the Linux arcade cabinet as an always-available development machine that can run Claude Code or Codex through T3 Code. After setup, the cabinet can be controlled from:

- The Mac laptop
- The Windows desktop
- A phone
- Any other device joined to the same Tailscale network

The agents, repositories, commands, and builds run on the arcade cabinet. The other devices are control interfaces.

## What will be installed

The cabinet needs:

1. Git and basic development utilities
2. Node.js 24
3. Claude Code
4. Codex CLI
5. Tailscale
6. T3 Code

Use one normal Linux account for T3 Code, Claude Code, Codex, and the project files. Do not run the agents or T3 as root.

## 1. Identify the Linux installation

Open a terminal on the cabinet or connect to it over SSH, then run:

```bash
cat /etc/os-release
uname -m
whoami
```

An `x86_64` architecture is ideal. Save the account name printed by `whoami`; this is the account that should own the repositories and run T3.

The examples below primarily target Ubuntu or Debian. If the cabinet uses Arch, Fedora, Bazzite, Batocera, or another distribution, use its equivalent package commands.

## 2. Install the basic tools

### Ubuntu or Debian

```bash
sudo apt update
sudo apt install -y git curl ca-certificates build-essential ripgrep openssh-server
sudo systemctl enable --now ssh
```

### Fedora

```bash
sudo dnf install -y git curl ca-certificates gcc gcc-c++ make ripgrep openssh-server
sudo systemctl enable --now sshd
```

### Arch Linux

```bash
sudo pacman -Syu --needed git curl ca-certificates base-devel ripgrep openssh
sudo systemctl enable --now sshd
```

Verify:

```bash
git --version
rg --version
ssh -V
```

## 3. Install Node.js 24

T3 remote environments currently require Node.js `^22.16`, `^23.11`, or `>=24.10`. Node.js 24 is the recommended choice.

Prefer a system-wide Node installation on this machine. Version managers such as `nvm` can disappear from noninteractive SSH sessions and systemd services unless their environment is configured explicitly.

After installing Node.js 24 using the package instructions for the cabinet's distribution, verify:

```bash
node --version
npm --version
npx --version
```

The Node result should be at least:

```text
v24.10.0
```

Also record the executable locations:

```bash
command -v node
command -v npm
command -v npx
```

## 4. Install Claude Code

Install the current native Linux build:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

If the installer adds Claude to the shell path, either restart the terminal or load the shell configuration it names. Then verify:

```bash
command -v claude
claude --version
```

Authenticate:

```bash
claude auth login
```

If a browser does not open on the cabinet, copy the displayed URL and open it on the Mac, Windows desktop, or phone. Paste the resulting code back into the cabinet terminal if prompted.

After login:

```bash
claude auth status
```

The authentication must be performed as the same Linux user that will run T3.

## 5. Install Codex CLI

Use the OpenAI installer:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Alternatively, install it through npm:

```bash
sudo npm install --global @openai/codex
```

Verify:

```bash
command -v codex
codex --version
```

For a cabinet or SSH session, device authentication is convenient:

```bash
codex login --device-auth
```

Open the displayed URL on another device and enter the code. Then verify:

```bash
codex login status
```

As with Claude, authenticate as the same Linux user that will run T3.

## 6. Install Tailscale

Tailscale gives the cabinet a stable private address and allows the laptop, desktop, and phone to reach it from home or elsewhere without exposing T3 directly to the public internet.

Install Tailscale using the instructions for the cabinet's distribution. On supported distributions, the standard installer is:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

Join the cabinet to the private network:

```bash
sudo tailscale up
```

Open the authentication link, sign into the same Tailscale account used on the Mac, Windows desktop, and phone, and then verify:

```bash
tailscale status
tailscale ip -4
```

Install the Tailscale app on the other three devices and join each one to the same tailnet.

## 7. Test T3 Code interactively

Start T3 on the cabinet:

```bash
npx --yes t3@latest serve --tailscale-serve
```

T3 should print a pairing URL, pairing token, and QR code. Leave this terminal running during the initial test.

If T3 cannot configure Tailscale Serve because the Linux user lacks permission, grant that account operator access:

```bash
sudo tailscale set --operator="$(whoami)"
```

Then retry:

```bash
npx --yes t3@latest serve --tailscale-serve
```

Do not bind T3 to the cabinet's public internet interface or forward port `3773` through the router.

## 8. Pair the controlling devices

### Mac or Windows T3 desktop application

1. Ensure Tailscale is connected.
2. Open T3 Code.
3. Open **Settings → Connections**.
4. Add a remote environment.
5. Paste the pairing URL printed by the cabinet.
6. Name it something recognizable, such as `Arcade Cabinet`.

### Phone

1. Connect the Tailscale app.
2. Scan the QR code printed by the cabinet, or open its pairing URL.
3. Save the T3 web interface to the home screen if desired.

The pairing URL and token are credentials. Do not publish or share them.

## 9. Add repositories

Create a dedicated development directory:

```bash
mkdir -p "$HOME/dev"
cd "$HOME/dev"
```

Clone a repository:

```bash
git clone YOUR_REPOSITORY_URL
cd YOUR_REPOSITORY_DIRECTORY
```

Confirm ownership:

```bash
ls -ld "$PWD"
```

The owner should be the same user running T3. If a repository was accidentally created by root, correct the specific repository:

```bash
sudo chown -R "$(whoami)":"$(id -gn)" /exact/path/to/repository
```

Add the project to T3 from the cabinet:

```bash
npx --yes t3@latest project
```

Remote project creation in the T3 GUI may still be limited, so adding projects from the cabinet CLI is the reliable route.

## 10. Confirm that T3 can see both providers

Stop and restart the interactive T3 server after authenticating the providers:

```bash
npx --yes t3@latest serve --tailscale-serve
```

From the Mac, Windows desktop, or phone:

1. Connect to `Arcade Cabinet`.
2. Open the added project.
3. Start a new thread.
4. Confirm that both Claude and Codex appear in the provider picker.
5. Ask each provider to run a harmless test such as:

```text
Report the current directory, show the Git branch, and list the top-level files. Do not modify anything.
```

If a provider is missing, run this diagnostic block on the cabinet:

```bash
whoami
command -v node
command -v npx
command -v claude
command -v codex
node --version
claude --version
claude auth status
codex --version
codex login status
```

The most common cause is that T3 is running as a different account or with a different `PATH` from the account used to install and authenticate the agents.

## 11. Keep T3 running after logout

First determine the actual `npx` path:

```bash
command -v npx
```

The example below assumes it prints `/usr/bin/npx`. If it prints something else, substitute that exact path in `ExecStart`.

Create the user-service directory:

```bash
mkdir -p "$HOME/.config/systemd/user"
```

Create `$HOME/.config/systemd/user/t3-code.service` with:

```ini
[Unit]
Description=T3 Code remote development server
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/dev
ExecStart=/usr/bin/npx --yes t3@latest serve --tailscale-serve
Restart=on-failure
RestartSec=5
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

If `claude` or `codex` is installed somewhere outside the listed `PATH`, add its directory to the `Environment=PATH=` line.

Load and start the service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now t3-code
systemctl --user status t3-code
```

Allow the user service to run when nobody is logged into the cabinet:

```bash
sudo loginctl enable-linger "$(whoami)"
```

View live logs:

```bash
journalctl --user -u t3-code -f
```

Restart after an update or configuration change:

```bash
systemctl --user restart t3-code
```

## 12. Prevent the cabinet from sleeping

The cabinet must remain powered on and awake for remote development.

Inspect the sleep targets:

```bash
systemctl status sleep.target suspend.target hibernate.target hybrid-sleep.target
```

On a dedicated always-on cabinet, sleep can be disabled with:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

This does not require the display to remain on. The display can blank while the computer continues running.

## 13. Updating

Claude's native installation normally handles its own updates. Check versions periodically:

```bash
claude --version
codex --version
node --version
```

Update Codex when installed through npm:

```bash
sudo npm install --global @openai/codex@latest
```

Because the T3 service uses `t3@latest`, restarting it lets `npx` resolve the current release:

```bash
systemctl --user restart t3-code
```

Finish active agent work before restarting T3.

## 14. Useful operating commands

```bash
# Check the private network
tailscale status

# Check T3
systemctl --user status t3-code

# Follow T3 logs
journalctl --user -u t3-code -f

# Restart T3
systemctl --user restart t3-code

# Check agent authentication
claude auth status
codex login status

# Manage T3 pairing credentials and revoke old sessions
npx --yes t3@latest auth
```

## Security notes

- Keep T3 on Tailscale rather than exposing it through router port forwarding.
- Treat T3 pairing links and tokens like passwords.
- Revoke pairings for devices you no longer control.
- Run T3 and the agents as a normal user, not root.
- Give the development user access only to files and services that the agents actually need.
- Keep arcade ROMs, emulator configuration, and development repositories in separate directories.
- Commit or back up important work before granting agents broad command permissions.

## Final verification checklist

- [ ] Cabinet is `x86_64` or otherwise supported by every required binary
- [ ] Node.js is at least `24.10`
- [ ] `git`, `rg`, `node`, `npm`, and `npx` work
- [ ] Claude Code is installed and authenticated
- [ ] Codex is installed and authenticated
- [ ] Tailscale connects the cabinet, Mac, Windows desktop, and phone
- [ ] T3 pairing works from all desired devices
- [ ] Both Claude and Codex appear in the T3 provider picker
- [ ] Repositories are owned by the T3 Linux user
- [ ] T3 starts automatically through the user service
- [ ] The cabinet does not suspend
- [ ] No T3 port is exposed directly to the public internet

## Primary references

- [T3 Code repository](https://github.com/pingdotgg/t3code)
- [T3 Code remote-access documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
- [Claude Code quickstart](https://docs.anthropic.com/en/docs/claude-code/quickstart)
- [Claude Code authentication](https://docs.anthropic.com/en/docs/claude-code/iam)
- [Codex CLI authentication](https://developers.openai.com/codex/auth)
- [Tailscale Linux installation](https://tailscale.com/kb/1031/install-linux)
