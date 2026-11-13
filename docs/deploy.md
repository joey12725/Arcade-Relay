# Deploying to real hardware

The sim proved the software (see docs/acceptance.md). This runbook
takes it to the two real machines: the **server** (any Debian/Ubuntu box on
your home network) and the **cabinet** (the x86 mini-PC currently running
Batocera — it gets reimaged). Steps are ordered; do the server first so the
cabinet has something to dial into.

Everything that can only be verified on real hardware is tracked in
docs/hardware-checklist.md — walk it top to bottom after first boot (§7).

---

## 1. Server install

```sh
git clone <this-repo> && cd Arcade-Relay
sudo ./server/provision/install.sh
```

What it does: installs runtime deps, copies `server/` to
`/opt/arcade/server`, puts `arcadectl` on PATH, seeds the **state repo** at
`/var/lib/arcade/state` from this checkout's `state/` (once — an existing
repo is never overwritten), and enables `arcaded.service` (Restart=always,
port 8000).

Sanity check:

```sh
systemctl status arcaded
arcadectl --json status        # {"online": false, ...} — no cabinet yet, fine
```

### 1.1 Plex (screensaver media source)

The screensaver plays shows and movies from a Plex Media Server. Point
arcaded at it with `PLEX_URL` in `/etc/systemd/system/arcaded.service`
(default `http://plex:32400`, which is what the container deploy uses), then
`systemctl daemon-reload && systemctl restart arcaded`.

- An unclaimed Plex on your LAN answers without a token; arcaded adds the
  server's subnet to Plex's allowed networks on first contact.
- A claimed server that requires auth: set `PLEX_TOKEN` (and
  `PLEX_ALLOW_LAN=0`). The token is sent as `X-Plex-Token` and nothing else
  changes.

arcaded does its own 480p / 1.5 Mbps ffmpeg transcode (tunable with
`SCREENSAVER_MAX_HEIGHT`, `SCREENSAVER_MAX_WIDTH`, `SCREENSAVER_VIDEO_BITRATE`),
so the tunnel budget is enforced server-side and Plex's own transcoder is
never involved. Pick sources with `arcadectl screensaver sources` and
`enable-source <id>`.

### 1.2 Game library

The ROM queue serves games from a folder you own. Lay it out by system and
point `ROM_SOURCE_DIR` at it in the unit (or the container environment):

```
/srv/games/
├── nes/        Star Patrol.nes, Some Homebrew (World).zip, ...
├── snes/
├── genesis/    (megadrive/ and md/ are accepted as aliases)
├── gba/
├── psx/        Game.cue + its .bin tracks, or a .zip holding both
└── arcade/     MAME .zip sets, kept intact
```

`arcadectl roms request "<title>" --system nes` then finds the file, stages
a sha256-verified copy (archives are CRC-checked and unpacked, CUE/GDI
descriptors must only reference tracks that shipped), delivers it to the
cabinet over the tunnel, verifies it again on arrival, fetches box art and
rescans Pegasus. Set `ROM_LIBRARY` (and optionally `ROM_LIBRARY_OWNER=uid:gid`)
to also file delivered games into a shared library folder. Without
`ROM_SOURCE_DIR` the queue serves a small generated test catalogue, which is
what the sim uses.

### 1.3 Cabinet-side ROMs project

Cabinet agents can submit requests to the server directly. The **ROMs**
project is `/roms`; provision installs its `README.md`, `AGENTS.md`,
and `skills/arcade-roms/SKILL.md` (linked as root
`SKILL.md` and through project-local `.agents`/`.claude` skill directories).
The same skill is installed user-wide for Claude Code (`~/.claude/skills`),
Codex (`~/.agents/skills`, plus legacy `~/.codex/skills`), and OpenCode
(`~/.config/opencode/skills`). Start a new agent session to load it; restart
OpenCode after skill installation.

`/usr/local/bin/arcadectl` reads `/etc/arcade/cabinet.env`'s `CABD_MEDIA_BASE`
outside interactive shells. The provisioner seeds that URL only when the
file does not already exist; `ARCADED_URL` remains an explicit override.
The cabinet needs no credentials for the game library.

For an existing cabinet, install just the client and documentation from an
updated checkout, without restarting services or running full provisioning:

```sh
sudo python3 cabinet/provision/install-rom-client.py
cd /roms
arcadectl --json status
arcadectl --json roms request "Star Patrol" --system nes
arcadectl --json roms status
```

Differing previous documentation/client files are backed up, and existing
skill entries are preserved. The installer does not modify game/save files.

## 2. Cloudflare Tunnel (one-time, interactive)

On the server:

```sh
# install cloudflared (Cloudflare's apt repo), then:
cloudflared tunnel login
cloudflared tunnel create arcade
cloudflared tunnel route dns arcade arcade.<your-domain>
cp /etc/cloudflared/config.yml.example /etc/cloudflared/config.yml
# edit: real <TUNNEL_ID>, your hostname(s)
systemctl enable --now cloudflared
```

Verify from anywhere: `curl https://arcade.<your-domain>/health` → `{"ok": true}`.

**Remote view caveat:** `arcadectl cab remote-view` opens a **raw TCP** VNC
listener on server port 5901. That does *not* ride the HTTP hostname. Either:

- VNC only from the server's LAN: `vnc://<server-lan-ip>:5901`, done; or
- add the `arcade-vnc` **TCP ingress** from the example config, then on the
  machine you're VNC-ing from:
  `cloudflared access tcp --hostname arcade-vnc.<your-domain> --url localhost:5901`
  and point your VNC client at `localhost:5901`.

## 3. Cabinet install (reimaging from Batocera)

**Before wiping Batocera:** copy off your ROMs and saves
(`/userdata/roms`, `/userdata/saves`) — OQ-3 migration.

1. Install **Debian stable, minimal** (netinst; no desktop task, no display
   manager). Get it on the cabinet's Wi-Fi.
2. Clone this repo and run:

```sh
sudo SERVER_HOST=arcade.<your-domain> ./cabinet/provision/install.sh
```

What it does: X11/Openbox kiosk with auto-login user `arcade` on tty1, all
blanking/DPMS off (the screensaver system owns idle), cabinet code at
`/opt/arcade/cabinet`, systemd **user** units `cabd`/`pegasus`/`steam`
(all Restart=always, started from boot via lingering), udev rules for stable
controller identities. `cabd` gets
`SERVER_WS_URL=wss://arcade.<your-domain>/cab` baked into its unit.

3. Manual installs (no Debian packages — this is the expected extra 20 min):
   - **Pegasus**: grab the latest `pegasus-fe` Linux release
     (pegasus-frontend.org), put the binary at `/usr/local/bin/pegasus-fe`.
     Theme + config:

     ```sh
     mkdir -p ~arcade/.config/pegasus-frontend/themes
     cp -r themes/pegasus-y2k ~arcade/.config/pegasus-frontend/themes/
     # The Y2K theme needs the asset pack (skins, sprites, tagged GIFs)
     # INSIDE the theme dir — it loads them by relative path, and without
     # them you get flat colours and no mascots.
     cp -r assets ~arcade/.config/pegasus-frontend/themes/pegasus-y2k/assets
     printf '%s\n' /roms/gb /roms/nes /roms/snes /roms/genesis /roms/arcade \
         /roms/gba /roms/psx /roms/favorites /roms/streaming /roms/steam \
         > ~arcade/.config/pegasus-frontend/game_dirs.txt
     # in Pegasus settings (or settings.txt): theme = pegasus-y2k
     chown -R arcade:arcade ~arcade/.config/pegasus-frontend
     ```

     `themes/pegasus-redblue` is still present as the plain fallback; set
     `theme = pegasus-redblue` if the Y2K theme misbehaves on the panel.

     Box art is not bundled — it is fetched by the server and pushed down:

     ```sh
     arcadectl art warm    # fill the server-side cache from libretro-thumbnails
     arcadectl art push    # deliver it to /roms/art on the cabinet
     ```

   - **Steam**: `dpkg --add-architecture i386 && apt update &&
     apt install steam-installer`. One-time login: see §5.
   - **Moonlight**: `apt install moonlight-qt` (available in Debian) or the
     flatpak. Pair with Sunshine/Apollo on the gaming desktop (same LAN).
   - **Greenlight** (xCloud, best-effort tier): AppImage from its releases
     page → `/usr/local/bin/greenlight`. Sign in once via remote view (§5).
4. Reboot. The cabinet should come up in Pegasus with the Y2K theme.

## 4. Pairing check (cabinet ↔ server)

On the server:

```sh
arcadectl status                 # ONLINE within seconds of cabinet boot
arcadectl state push             # push full state; watch cabd apply it
arcadectl cab logs -f            # live cabd log while you poke around
arcadectl games list             # inventory scanned from /roms
```

Copy your ROMs into `/roms/<system>/` on the cabinet (scp/rsync), then
`arcadectl roms sync` to trigger a Pegasus rescan. Saves restored to
`/saves/` are backed up to the server automatically (hourly content-diff;
`curl -s localhost:8000/saves/status` on the server to confirm).

## 5. One-time interactive logins (Steam, Xbox)

```sh
arcadectl cab remote-view        # prints the VNC port/URL
```

VNC in (see §2 caveat), do the Steam login / Greenlight Microsoft sign-in on
the cabinet's screen, then `arcadectl cab remote-view --off`. This is the
only time a keyboard-ish interaction is ever needed; it happens from your
couch, not at the cabinet.

## 6. Smart-home / agent hooks

The REST API is deliberately plain HTTP on the tunnel hostname (AGT-5), e.g.
nightly quiet hours from Home Assistant / cron:

```sh
curl -X POST https://arcade.<your-domain>/screensaver/set \
     -H 'Content-Type: application/json' -d '{"brightness": 30}'
```

Agents: point them at `AGENTS.md` in this repo and give them
`arcadectl` — every command supports `--json`, exit codes are meaningful,
`skills/` has worked examples per domain.

## 7. First-boot hardware walkthrough

Work through **docs/hardware-checklist.md** in order. The high-value early items:

1. Panel input: `arcadectl controls monitor --for 10` while mashing buttons —
   every button should stream events. Fix udev ids in
   `/etc/udev/rules.d/99-arcade-controllers.rules` if devices are missing
   (`lsusb`, then `udevadm control --reload`).
2. Combos: hold Start 2s in a game → back to Pegasus (HOME). Start+Select 5s
   → lifeline screen (and again to exit).
3. Real cores: `arcadectl games launch <something>` per system; check
   performance, set `video_driver`/cores in `state/cabinet.yaml`
   (`emulators:` map) and `arcadectl state push`.
4. Panel fit: confirm native resolution, screensaver panscan crop, theme
   readability from feet away.
5. Network truth: pull the router's plug for 10 minutes mid-game — gameplay
   must be untouched, cabinet reconnects by itself (this is soak-proven in
   sim; verify once on the real link).

## 8. Rollback & recovery

- **Config mistake:** every change is a git commit in the state repo.
  `arcadectl state log` → `arcadectl state rollback <sha>` → re-renders and
  pushes to the cabinet. `arcadectl state diff` shows what the last change did.
- **Bad code deploy (server):** /opt/arcade holds a copy, not a git repo —
  your clone does the versioning. `git log` in the clone, check out the
  known-good commit, re-run `server/provision/install.sh`, then
  `systemctl restart arcaded`. State repo is untouched by code deploys.
- **Bad code deploy (cabinet):** same idea — check out the good commit and
  re-run `cabinet/provision/install.sh` (refreshes /opt/arcade/cabinet and
  reloads unit files). Running daemons keep executing the OLD code until
  restarted: follow up with
  `arcadectl cab exec -- systemctl --user restart cabd pegasus` (or reboot).
- **Cabinet unreachable:** lifeline combo on the panel (Start+Select 5s)
  shows exactly which layer is broken (link/DNS/internet/WS) with red/blue
  indicators, drops reconnect backoff to 1s, and keeps SSH up.
- **Saves:** server keeps `/var/lib/arcade/saves-backup/latest.tar.gz`
  (content-addressed, updated on change). Restore = untar into `/saves/` on
  the cabinet.
- **Full cabinet loss:** reimage (§3) + `arcadectl state push` + restore
  saves + copy ROMs back. Nothing authoritative lives on the cabinet.

---

*Appendix: saves wiring.* `state/cabinet.yaml` renders
`savefile_directory`/`savestate_directory = /saves` into retroarch.cfg, so
RetroArch writes where the hourly backup loop watches. If you add non-libretro
emulators later, point their save paths at `/saves/<system>/` too.

*Appendix: lifeline recovery.* While lifeline mode is up, cabd tries to bring
the cabinet back by itself: ensures SSH is listening, brings up a fallback
transport if configured, and restarts networking when the panel's `a` button
is pressed (remotely: `arcadectl cab lifeline restart-network`). Override the
commands per machine with `LIFELINE_SSH_CMD`, `LIFELINE_TRANSPORT_CMD` (e.g.
`tailscale up`), `LIFELINE_NET_RESTART_CMD` in `cabd.service`.

*Appendix: per-game controls.* Three mapping layers, most specific wins:
`--scope global`, `--scope system:<sys>`, `--scope game:<sys>/<rom>`. Per-game
overrides live in `state/controllers/games.yaml` and are merged over the
system layer into the remap file rendered before each launch. The web UI's
remapper does this interactively: pick scope, press the physical button, click
the logical target.

---

*Appendix: agentic development on the cabinet.* `cabinet/provision/devtools.sh`
(run automatically by install.sh) installs git + build tools, Node 24, Claude
Code (native build), Codex CLI, Tailscale, ripgrep/jq/tmux, writes the
`t3-code.service` user unit, enables lingering, and masks sleep/suspend so the
cabinet stays reachable. It never fails cabinet provisioning: any tool that
can't install is reported and skipped.

The interactive half — `tailscale up`, `claude auth login`,
`codex login --device-auth`, and T3 pairing — must happen once on the cabinet.
`docs/t3-setup.md` is the full guide; docs/hardware-checklist.md has the ordered
checklist. Two things that bite: the agents must be authenticated as the
**same Linux user** that runs T3, and T3 needs Node **≥24.10** (devtools
asserts this and fails loudly if the installed Node is older).

Keep T3 on Tailscale — do not port-forward 3773. Keep ROMs/emulator config
and dev repositories in separate directories (`/roms` vs `~/dev`).
