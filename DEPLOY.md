# DEPLOY.md — putting this on the real hardware

The sim proved the software (see DONE.md, state/JOURNAL.md). This runbook
takes it to the two real machines: the **server** (any Debian/Ubuntu box on
your home network) and the **cabinet** (the x86 mini-PC currently running
Batocera — it gets reimaged). Steps are ordered; do the server first so the
cabinet has something to dial into.

Everything that can only be verified on real hardware is tracked in
HARDWARE-TODO.md — walk it top to bottom after first boot (§7).

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

### 1.1 Jellyfin (screensaver media source)

Install Jellyfin on the server (official repo or Docker, your call), listening
on `http://localhost:8096` (or set `JELLYFIN_URL` in
`/etc/systemd/system/arcaded.service` and `systemctl daemon-reload &&
systemctl restart arcaded`). arcaded bootstraps it on first contact: completes
the startup wizard, creates the `arcade`/`arcade` user, and creates
`Shows`/`Movies` libraries if missing. Point those libraries at your media.
The screensaver pulls 480p-capped transcodes; nothing else to configure.

If your Jellyfin is already set up, either let arcaded use the
`arcade`/`arcade` account or set `JELLYFIN_TOKEN` in the unit instead.

### 1.2 ROM pipeline

`roms search/request` ships against the ROMDL contract (ASSUMPTIONS A-5) with
a built-in fake catalog. Wire your real downloader by replacing
`/opt/arcade/server/romdl.py`'s `search()`/`download()` — the interface is
documented at the top of that file. Everything downstream (staging, checksum,
tunnel delivery, rescan, `roms status`) is already real and soak-tested.

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
     cp -r themes/pegasus-redblue ~arcade/.config/pegasus-frontend/themes/
     printf '%s\n' /roms/gb /roms/nes /roms/snes /roms/genesis /roms/arcade \
         /roms/gba /roms/psx /roms/favorites /roms/streaming /roms/steam \
         > ~arcade/.config/pegasus-frontend/game_dirs.txt
     # in Pegasus settings (or settings.txt): theme = pegasus-redblue
     chown -R arcade:arcade ~arcade/.config/pegasus-frontend
     ```

   - **Steam**: `dpkg --add-architecture i386 && apt update &&
     apt install steam-installer`. One-time login: see §5.
   - **Moonlight**: `apt install moonlight-qt` (available in Debian) or the
     flatpak. Pair with Sunshine/Apollo on the gaming desktop (same LAN).
   - **Greenlight** (xCloud, best-effort tier): AppImage from its releases
     page → `/usr/local/bin/greenlight`. Sign in once via remote view (§5).
4. Reboot. The cabinet should come up in Pegasus with the red/blue theme.

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

Agents: point them at `CLAUDE.md` / `AGENTS.md` in this repo and give them
`arcadectl` — every command supports `--json`, exit codes are meaningful,
`skills/` has worked examples per domain.

## 7. First-boot hardware walkthrough

Work through **HARDWARE-TODO.md** in order. The high-value early items:

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
