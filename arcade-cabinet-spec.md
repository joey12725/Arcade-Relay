# Arcade Cabinet Platform — Full Specification

**Version:** 0.1 (draft for review)
**Date:** 2026-07-22
**Owner:** Joey
**Audience:** Coding agents (Claude Code, Codex, etc.) and human collaborators. This document is intended to be implementation-ready: an agent should be able to pick a milestone and build it.

---

## 1. Vision

A two-machine system: a low-horsepower **arcade cabinet** (x86 mini-PC, currently Batocera — to be reimaged) and a **server** on a different network, linked over the internet via a Cloudflare Tunnel. A separate **gaming desktop** shares the cabinet's LAN (Wi-Fi 6E).

The cabinet is a beautiful, dead-simple appliance: big highly-visible icons, red/blue color scheme, favorites front and center, a handful of physical buttons. Everything else — settings, volume, controller mapping, screensaver configuration, ROM management, diagnostics — is controlled **remotely from the server side**, because the cabinet almost never has a keyboard or mouse attached.

The control plane is **agent-first**: a real CLI (`arcadectl`) plus SKILL.md files, so Claude Code / Codex / any agent can be pointed at the server and asked for what you want in plain language. Explicitly **not MCP**. A web UI exists but is secondary to the CLI.

The non-negotiable core: **absolutely unshakably reliable retro game emulation.** Everything else layers on top and must never compromise it.

### 1.1 Prime directives (design principles)

1. **Local-first emulation.** Games, emulators, save files, and controller configs live on the cabinet. Emulation must work perfectly with the network cable cut. No launch path may block on the server.
2. **Server-authoritative control.** All configuration originates on the server and is pushed/synced to the cabinet. The cabinet exposes essentially no on-device settings UI (except Lifeline mode, §5.11).
3. **Config as code.** Every mutable setting (mappings, audio policy, screensaver params, theme options) is a declarative file in a version-controlled state repo on the server. The cabinet daemon renders these into tool-specific config files *before every launch*, stomping drift. This is the antidote to the "Batocera scrambles my controller configs" problem.
4. **Agent-first interfaces.** Anything a human can do in the web UI, an agent can do via `arcadectl` with `--json` output. Skills document the workflows. No MCP.
5. **Security is explicitly out of scope.** By owner decree. A single shared bearer token (or nothing) is acceptable. Do not spend implementation effort on auth, TLS beyond what Cloudflare provides for free, or permissions.
6. **Graceful degradation.** Server unreachable → games still launch, screensaver falls back to local clips (or a dim clock), and the cabinet aggressively reconnects forever.

---

## 2. System topology

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│  CABINET LAN (Wi-Fi 6E)     │        │  SERVER (different network)  │
│                             │        │                              │
│  ┌───────────┐              │        │  arcaded  (control daemon)   │
│  │ Cabinet   │◄─────────────┼────────┤  arcadectl (CLI)             │
│  │  cabd     │  wss:// via  │        │  Web UI                      │
│  │  Pegasus  │  Cloudflare  │        │  Jellyfin (media)            │
│  │  RetroArch│  Tunnel      │        │  ROMDL (ROM pipeline)        │
│  │  mpv      │              │        │  skills/ (SKILL.md files)    │
│  └─────▲─────┘              │        │  state repo (git)            │
│        │ Moonlight (LAN)    │        └──────────────────────────────┘
│  ┌─────┴─────┐              │
│  │ Desktop   │  Sunshine/   │        ┌──────────────────────────────┐
│  │ (gaming   │  Apollo host │        │  Xbox Cloud (xCloud)         │
│  │  PC)      │              │        │  via Greenlight (internet)   │
│  └───────────┘              │        └──────────────────────────────┘
└─────────────────────────────┘
```

- **Cabinet → Server:** persistent outbound WebSocket (survives NAT; nothing inbound needed on the cabinet's network). Server is reachable at a stable hostname through a Cloudflare Tunnel. All control traffic, config sync, ROM delivery, and 480p media streaming ride this path.
- **Cabinet ↔ Desktop:** LAN-only, for Steam game streaming (Moonlight client on cabinet, Sunshine/Apollo host on desktop).
- **Cabinet ↔ Internet:** Xbox Game Pass cloud streaming via Greenlight.
- **Agents:** run on/against the server (SSH in, or run locally on the server), use `arcadectl` + skills.

---

## 3. Requirements inventory

Every requirement from the source conversation, given a stable ID for traceability. **P0** = core, must ship; **P1** = important; **P2** = nice-to-have / research.

### Emulation (EMU)

| ID | Req | Pri |
|----|-----|-----|
| EMU-1 | Unshakably reliable retro game emulation is the single most important property of the system. | P0 |
| EMU-2 | Games launch fast, and it is *very* easy to get back to the home menu using the cabinet's limited buttons. | P0 |
| EMU-3 | A crashed/hung game never strands the cabinet; the system always returns to the frontend (watchdog). | P0 |
| EMU-4 | Local Steam games (small titles that run fine on the hardware, e.g. Celeste) are launchable from the same UI. | P1 |

### Frontend / UI (UI)

| ID | Req | Pri |
|----|-----|-----|
| UI-1 | Gorgeous UI: big, highly visible icons; red and blue color scheme. | P0 |
| UI-2 | Favorites are first-class: trivially easy to add/remove favorites, and **favorites are the default view** on boot. | P0 |
| UI-3 | Prefer adopting/theming an existing frontend rather than building one from scratch ("hijack somebody else's UI"). | P0 |
| UI-4 | Navigation works with a very limited button set (joystick + a few buttons; no keyboard/mouse). | P0 |

### Screensaver / attract mode (SCR)

| ID | Req | Pri |
|----|-----|-----|
| SCR-1 | When idle, the cabinet streams series/movies from the server's media library at 480p. | P0 |
| SCR-2 | Video is cropped to fill the 4:3 display — no black bars, no squishing; center-crop to aspect. | P0 |
| SCR-3 | Screensaver brightness is settable 0–200% (0% = black, 100% = original/default, 200% = white). | P0 |
| SCR-4 | Individual series and movies can be enabled/disabled as screensaver sources, remotely. | P0 |
| SCR-5 | All screensaver parameters (sources, brightness, idle timeout, etc.) are exposed via CLI/UI. | P0 |
| SCR-6 | A keybind (or at minimum an exposed command) force-triggers screensaver mode and force-disables it. | P0 |
| SCR-7 | The anime-clip screensaver plays with **no audio** by default (see AUD-2). | P0 |

### Audio (AUD)

| ID | Req | Pri |
|----|-----|-----|
| AUD-1 | Cabinet master volume is remotely controllable. | P0 |
| AUD-2 | Per-class audio policy: **games get volume; trailers get no volume; screensaver gets no volume** — each class independently adjustable/mutable. | P0 |

### Controllers (CTL)

| ID | Req | Pri |
|----|-----|-----|
| CTL-1 | Controller remapping per-system **and** globally. | P0 |
| CTL-2 | Save and load named remapping presets. | P0 |
| CTL-3 | A good UI for remapping (web UI matters more here than most places). | P0 |
| CTL-4 | Mappings must be durable — the current pain is configs getting scrambled constantly in Batocera. | P0 |
| CTL-5 | Bluetooth controller support: explicitly deferred (may not even have a BT module). | P2 |

### Agent interface (AGT)

| ID | Req | Pri |
|----|-----|-----|
| AGT-1 | The entire system is drivable by coding agents (Claude Code, Codex, etc.) pointed at the server. | P0 |
| AGT-2 | **Not MCP.** Interface = CLI tools + SKILL.md files. | P0 |
| AGT-3 | Web UI exists and is nice, but agent access is the higher priority. | P0 |
| AGT-4 | Deep, deep control: if it's on Wi-Fi, essentially everything on the cabinet is remotely manipulable. | P0 |
| AGT-5 | Exposed parameters (esp. screensaver brightness/enable) are consumable by smart-home automations (e.g., Home Assistant dims the cabinet at night via REST/CLI). | P1 |

### Game streaming (STR)

| ID | Req | Pri |
|----|-----|-----|
| STR-1 | Steam game streaming from the desktop over LAN (e.g., Street Fighter 6), since the cabinet lacks horsepower. | P1 |
| STR-2 | Xbox Game Pass **cloud** streaming (xCloud) support — research item; fine if impossible. | P2 |
| STR-3 | Xbox **home console** streaming support ("and home, for that matter") — research item. | P2 |

### ROM pipeline (ROM)

| ID | Req | Pri |
|----|-----|-----|
| ROM-1 | Server-side integration with the existing **ROMDL** package (owner's ROM scraper/downloader). | P1 |
| ROM-2 | Queue up and request games remotely; requests flow: request → download on server → deliver to cabinet → appear in frontend. | P1 |
| ROM-3 | The whole pipeline is agentically exposed ("get me Metal Slug 3" should be a one-liner to an agent). | P1 |

### Connectivity & recovery (NET)

| ID | Req | Pri |
|----|-----|-----|
| NET-1 | Server reachable via Cloudflare Tunnel; cabinet connects outbound. | P0 |
| NET-2 | On disconnect, the cabinet does everything in its power to reconnect, forever. | P0 |
| NET-3 | **Lifeline mode**: a keybind/settings toggle puts the cabinet in a mode that displays internet + server connectivity status and makes it maximally easy to re-establish control from the server side. | P0 |
| NET-4 | Most settings live off-cabinet; on-cabinet settings surface is minimal (basically just Lifeline mode). | P0 |

### Server (SRV)

| ID | Req | Pri |
|----|-----|-----|
| SRV-1 | Server hosts the control daemon, CLI, web UI, media library (Jellyfin), ROM pipeline, and skills. | P0 |
| SRV-2 | All state versioned in a git repo (config as code). | P0 |

---

## 4. Technology decisions

Decisions an implementing agent should treat as settled unless the owner overrides them. Rationale included so future changes are informed.

| Area | Decision | Rationale / alternatives considered |
|------|----------|-------------------------------------|
| Cabinet OS | **Debian stable, minimal install, X11 + Openbox kiosk** (auto-login, no DE) | Full rebuild approved. A general-purpose distro (vs. Batocera's read-only appliance image) is what makes deep remote control, custom daemons, and config-as-code practical. X11 chosen over Wayland for mature scriptable window layering (`xdotool`, `wmctrl`) — the screensaver/tint/frontend stack is a window-stacking problem. Wayland (sway/cage) is a viable future migration. |
| Frontend | **Pegasus Frontend** with a fully custom QML theme | Satisfies UI-3 ("hijack somebody else's UI") while allowing total visual control: QML themes can implement red/blue, huge icons, and a favorites-default home grid. ES-DE rejected (theming less flexible, no clean programmatic surface); Batocera-ES rejected (ties us back to Batocera); fully-custom frontend rejected (too much work for v1). Pegasus is open source; fork if needed. |
| Emulation | **RetroArch** (libretro cores) as primary; standalone emulators only where a core is inadequate | One config surface, per-core/per-game remap files, JSON-ish configs that render cleanly from the state repo, and a **network command interface** (UDP) for pause/quit/etc. Aligns with EMU-1 and CTL-4. |
| Screensaver player | **mpv** with JSON IPC socket | Rock-solid, plays Jellyfin HLS/direct streams, runtime-controllable (load, stop, crop, dim) via a Unix socket. `--panscan=1.0` gives exact "crop to fill, never squish" behavior for SCR-2. |
| Audio | **PipeWire** with WirePlumber; per-app-class routing/volume via `wpctl` | Per-node volume control lets the daemon enforce the class policy (games / trailers / screensaver) by matching client process → class. Modern default on Debian. |
| Media server | **Jellyfin** (recommend over Plex) | Free, clean REST API with API keys, transcoding parameters controllable per-stream (cap at 480p), easy per-library/series enumeration for SCR-4. If Plex is already entrenched, an adapter is possible but Jellyfin is the spec'd path. |
| Steam LAN streaming | **Sunshine** (or its fork **Apollo**) host on the desktop; **Moonlight-qt** client on the cabinet | The standard self-hosted pair; low-latency on Wi-Fi 6E; excellent gamepad passthrough; per-app "launch SF6 directly" entries. Steam Link considered but Sunshine/Moonlight is more controllable/scriptable. |
| Xbox streaming | **Greenlight** (open-source xCloud + Xbox home streaming client; Electron, runs on Linux) | Verified actively maintained (v2.4.2, June 2026). Supports both Game Pass cloud streaming (STR-2) and home console streaming (STR-3). Treated as best-effort: it's an unofficial client and can break when Microsoft changes things — acceptable per requirements ("no big deal if not"). |
| Local Steam | Steam client on cabinet, launched per-game via `steam steam://rungameid/<id>` from Pegasus entries | Small indie titles only (EMU-4). |
| Cabinet↔Server transport | Persistent **WebSocket** (cabinet dials out) through **Cloudflare Tunnel**; JSON-RPC-style messages; HTTP(S) through the same tunnel for file/media transfer | NET-1/NET-2. Nothing needs to be exposed on the cabinet's network. Note: streaming video through Cloudflare is fine at 480p bitrates; if it ever becomes a problem, Tailscale is the drop-in fallback transport (config change, not architecture change). |
| Server stack | Python (FastAPI + click/typer) or Go — implementer's choice; single repo | `arcaded` (daemon + REST + WS hub), `arcadectl` (CLI hitting the same REST API), web UI (any lightweight SPA) all in one repo with `skills/`. |
| State | Git repo of YAML/TOML files on the server (`state/`) | SRV-2, CTL-4. Every change is a commit → free history/rollback ("what scrambled my controls" becomes `git log`). |

### 4.1 Repository layout (target)

```
arcade/
├── README.md, AGENTS.md, CLAUDE.md      # agent onboarding docs
├── skills/                              # SKILL.md files (see §7.3)
│   ├── screensaver/SKILL.md
│   ├── audio/SKILL.md
│   ├── controllers/SKILL.md
│   ├── roms/SKILL.md
│   ├── games/SKILL.md
│   └── diagnostics/SKILL.md
├── server/                              # arcaded + arcadectl + web UI
├── cabinet/                             # cabd daemon + provisioning (ansible or shell)
├── state/                               # THE source of truth (git-tracked)
│   ├── audio.yaml                       # class volumes/mutes, master
│   ├── screensaver.yaml                 # sources, brightness, timeout
│   ├── controllers/
│   │   ├── active.yaml                  # which preset is live, per-scope
│   │   └── presets/<name>.yaml
│   ├── library/                         # game metadata, favorites
│   │   └── favorites.yaml
│   └── cabinet.yaml                     # hostname, display, paths, hotkeys
└── themes/pegasus-redblue/              # custom QML theme
```

---

## 5. Cabinet — component specifications

### 5.1 Base image & process model

- Debian stable, auto-login user `arcade`, X11 + Openbox, no display manager frills. Disable all power management/blanking (`xset s off -dpms`); the screensaver system owns idle behavior.
- systemd user units, all `Restart=always`:
  - `cabd.service` — the cabinet daemon (below). Everything else is a child or peer it supervises.
  - `pegasus.service` — the frontend. cabd monitors and restarts it.
  - `steam.service` — Steam in the background, silent (`-silent`), so `rungameid` launches are instant. Optional; may be started lazily.
- mpv (screensaver), RetroArch, Moonlight, Greenlight are launched on demand by cabd, not persistent services.
- The cabinet keeps **no authoritative config**. On boot and on every `state-sync` push, cabd renders the state repo into concrete configs (RetroArch remaps, Pegasus theme settings, PipeWire policy) and overwrites local copies. Local edits are expected to be stomped.

### 5.2 `cabd` — the cabinet daemon (the heart of the system)

Single process (Python or Go) responsible for:

1. **Server link.** Maintain the outbound WebSocket to `wss://<server-host>/cab`. Reconnect with exponential backoff capped at ~10s, jittered, forever (NET-2). Heartbeat every 5s; the server marks the cabinet offline after 3 missed beats.
2. **Command execution.** Handle RPC messages (§8) — launch/kill games, set volumes, control the screensaver, apply configs, run diagnostics, take screenshots, reboot, arbitrary shell (`system.exec`, because security is out of scope and agents will need escape hatches).
3. **Idle tracking & screensaver orchestration.** Track input activity via evdev; after `screensaver.idle_seconds` with no input and no game running, start screensaver mode. Any joystick/button input exits it instantly back to the frontend.
4. **Global hotkeys.** Grab combos from evdev *regardless of focused app*:
   - `HOME_COMBO` (e.g., hold P1 Start 2s, exact binding in `state/cabinet.yaml`): kill foreground game/stream session, return to Pegasus (EMU-2).
   - `SCREENSAVER_COMBO`: force-toggle screensaver (SCR-6).
   - `LIFELINE_COMBO` (deliberately awkward, e.g., hold P1 Start + P2 Start 5s): enter Lifeline mode (NET-3).
5. **Watchdog.** If a launched game's process dies or its window disappears, ensure focus returns to Pegasus within 2s (EMU-3). If Pegasus itself dies, restart it.
6. **Audio policy enforcement.** See §5.6.
7. **Local state cache.** Last-synced copy of `state/` kept on disk so the cabinet boots and runs fully offline (prime directive 1, 6).

### 5.3 Frontend (Pegasus + `pegasus-redblue` theme)

- Custom QML theme implementing: red/blue palette, oversized box art tiles (readable from feet away), **Favorites as the boot/default collection** (UI-2), then per-system collections, then "Streaming" (Moonlight/Greenlight entries) and "Steam" collections.
- Favoriting: mapped to a single dedicated button in the frontend; state written to Pegasus favorites *and* mirrored to `state/library/favorites.yaml` by cabd (bidirectional sync, server wins on conflict).
- Game trailers/preview videos in the theme play **muted** always (AUD-2 makes this policy; the theme also just sets muted=true defensively).
- Launching: Pegasus launch commands go *through cabd* (`cabd-launch <system> <path>`) rather than exec'ing emulators directly, so cabd always knows what's running, can enforce configs pre-launch, and can kill cleanly.
- Navigation audit (UI-4): every reachable screen must be operable with joystick + 2 buttons (accept/back). Anything requiring more is a theme bug.

### 5.4 Emulation layer

- RetroArch with curated cores per system (exact system list: open question OQ-3; assume the classic 8/16-bit through PS1-era set initially, constrained by cabinet horsepower).
- Before every launch, cabd renders: global `retroarch.cfg` overrides, per-core remap files, per-game remaps if defined — from `state/controllers/` (CTL-1, CTL-4).
- RetroArch network command interface enabled on localhost UDP for pause/quit/menu/screenshot control.
- Save files/states on cabinet disk, rsynced up to the server opportunistically (backup, not dependency).
- Acceptance bar for EMU-1: 100 consecutive scripted launch→play-5s→exit cycles across every configured system with zero hangs, zero config drift, zero input loss.

### 5.5 Screensaver engine

**Behavior.** After idle timeout (default 300s, configurable), cabd starts mpv fullscreen above Pegasus, playing a shuffled queue drawn from the enabled sources. Episodes/movies resume-aware is *not* required; random mid-library selection is fine (configurable: `mode: shuffle_episodes | continue_series`).

**Source & quality (SCR-1).** cabd asks arcaded for a playlist; arcaded resolves enabled sources against Jellyfin and returns stream URLs with transcoding capped at **480p** (Jellyfin `VideoStreams` with `maxHeight=480` + sane bitrate cap, ~1.5 Mbps default, configurable). The 480p cap is about being kind to the server's uplink through the tunnel.

**Aspect handling (SCR-2).** mpv launched with `--fs --panscan=1.0 --keepaspect`: pan-scan crops the 16:9 source symmetrically to fill the 4:3 panel with zero bars and zero distortion. (Assumption A-1: the cabinet display is natively 4:3 — confirm resolution, OQ-1.)

**Brightness (SCR-3).** Multiplicative dim via mpv video filter: `--vf=lavfi=[colorchannelmixer=rr=F:gg=F:bb=F]` where `F = brightness/100`. Runtime-adjustable through mpv's JSON IPC socket without restarting playback. 100 → untouched image (default); 0 → black screen (still "on", by design). Above 100, `lutrgb` blends each channel toward white as `val*(2-F)+maxval*(F-1)`; 200 → white.

**Sources (SCR-4).** `state/screensaver.yaml`:

```yaml
idle_seconds: 300
brightness: 100         # 0-200; 100 is the default
mode: shuffle_episodes
sources:
  - jellyfin_id: "abc123"   # series: Cowboy Bebop
    enabled: true
  - jellyfin_id: "def456"   # movie: Redline
    enabled: false
fallback: local_clips    # local_clips | dim_clock | off  (used when server unreachable)
```

`arcadectl screensaver sources` lists every series/movie in the Jellyfin library with enabled flags; enabling/disabling is one command (§7.2).

**Audio (SCR-7).** The mpv process belongs to audio class `screensaver` → muted by default policy.

**Force control (SCR-6).** `SCREENSAVER_COMBO` on-cabinet; `arcadectl screensaver on|off|toggle` remotely; plus `arcadectl screensaver disable --for 2h` (temporary suppression, e.g. during a party).

**Offline fallback.** If the server is unreachable, play from a small local cache of pre-synced clips (`fallback: local_clips`; arcaded keeps ~2GB of 480p files pre-pushed to the cabinet), else dim clock.

### 5.6 Audio policy engine (AUD-1, AUD-2)

- PipeWire. cabd tags every audio client it launches (env `PULSE_PROP_media.role` / pipewire node props) into a **class**: `games` (RetroArch, Moonlight, Greenlight, Steam), `screensaver` (mpv), `trailers` (Pegasus theme video), `system` (everything else).
- `state/audio.yaml`:

```yaml
master: 70            # 0-100
classes:
  games:      {volume: 100, muted: false}
  trailers:   {volume: 100, muted: true}
  screensaver:{volume: 100, muted: true}
  system:     {volume: 50,  muted: false}
```

- cabd applies the policy on boot, on state-sync, and reactively whenever a new PipeWire node appears (subscribe to node events; classify by binary name/props; set node volume/mute within 100ms of appearance — no audible blip of trailer audio).
- Master volume maps to the default sink volume. All of it settable live via `arcadectl volume ...`.

### 5.7 Controller mapping manager (CTL-1..4)

**Model.** Three mapping scopes, layered (most specific wins):

1. **Global** — physical panel/encoder buttons → logical pad (SDL gamecontroller mapping + udev naming rules so devices get stable identities across reboots).
2. **Per-system** — logical pad → per-core RetroArch remap (e.g., SNES vs. Neo Geo button layouts).
3. **Per-game** — optional per-game remap files.

**Presets (CTL-2).** A preset = a named snapshot of all three layers: `state/controllers/presets/<name>.yaml`. `arcadectl controls save-preset tourney`, `arcadectl controls load-preset default`. Loading re-renders configs and takes effect on next launch (or immediately via RetroArch network command reload where possible).

**Durability (CTL-4).** Emulators never own mapping state. Rendered configs are regenerated from the state repo before every single launch — if anything scrambles a config at runtime, the next launch is clean. Every preset change is a git commit.

**UI (CTL-3).** The web UI's flagship page: pick scope → pick controller → interactive remap (press physical button, click logical target), live-tested against evdev events relayed from cabd (`input.monitor` RPC streams button presses to the browser). Same capability via CLI: `arcadectl controls map --system snes --button a=b ...` for agents.

**Bluetooth (CTL-5):** out of scope for v1. Design keeps it cheap later (udev/SDL layers are BT-agnostic).

### 5.8 Game streaming

**Steam from desktop (STR-1).**
- Desktop (Windows assumed, OQ-5) runs Sunshine or Apollo as a service, apps configured per-game (e.g., "Street Fighter 6") plus a generic "Steam Big Picture" entry.
- Cabinet runs Moonlight-qt. Pegasus "Streaming" collection contains one entry per Sunshine app, launched via `cabd-launch stream moonlight <app>` → `moonlight stream <desktop-host> "<app>" --resolution ... --fps 60`.
- LAN-only (desktop and cabinet share the Wi-Fi 6E network); does not transit the tunnel. `HOME_COMBO` quits the Moonlight session cleanly.
- Audio class: `games`.

**Xbox (STR-2, STR-3).**
- Greenlight on the cabinet, launched from Pegasus entries ("Game Pass Cloud", or specific pinned titles if deep-linkable). Sign-in is a one-time interactive step — do it via the web-UI remote-view/VNC (§5.10) so no keyboard is needed at the cabinet.
- Best-effort tier: if Microsoft breaks the unofficial client, the tile shows "unavailable" and nothing else in the system cares.
- Home-console streaming works through the same client if/when an Xbox console exists on a reachable network (OQ-6).

### 5.9 Local Steam (EMU-4)

- Steam installed on cabinet, logged in (one-time interactive setup via remote view), auto-started silent.
- Curated allowlist of installed lightweight titles in `state/library/steam.yaml`; Pegasus "Steam" collection generated from it; launch via `steam steam://rungameid/<id>`; cabd tracks the game window for watchdog/home-combo handling.

### 5.10 Remote hands (deep control, AGT-4)

Because there's essentially never a keyboard attached, cabd provides:

- `system.screenshot` → PNG of current display (agents *see* the cabinet).
- `system.exec` → arbitrary shell with stdout/stderr capture.
- VNC/remote-view on demand (`arcadectl cab remote-view` starts x11vnc, tunneled through the server connection, URL printed / surfaced in web UI) — for one-time interactive chores like Steam/Xbox logins.
- Synthetic input injection (`input.press home`, `input.text ...` via xdotool) for anything scriptable.
- `system.reboot`, `system.restart-frontend`, journal/log tailing (`arcadectl cab logs -f`).

### 5.11 Lifeline mode (NET-3, NET-4)

A dead-simple fullscreen status screen (part of cabd, no dependencies on Pegasus or the network stack it's diagnosing):

- Shows, with huge red/blue good/bad indicators: link-local network up? → internet reachable (ping 1.1.1.1)? → DNS? → server WebSocket connected? Plus cabinet IP, SSID, uptime, cabd version, last-sync time.
- While active, reconnect backoff drops to 1s (maximum aggression), and the cabinet additionally brings up every recovery path it can: ensures SSH is listening, attempts the fallback transport if configured (e.g., Tailscale), restarts networking on a button press.
- Entered via `LIFELINE_COMBO` on the panel; exited the same way or remotely. This is deliberately the **only** on-cabinet "settings" surface — everything else lives server-side (NET-4).

---

## 6. Server — component specifications

### 6.1 `arcaded` (control daemon)

- Hosts: the WebSocket hub the cabinet dials into; a REST API (the *only* API — CLI, web UI, and any future surface all consume it); the state repo (reads/writes + git commits with attributed messages, e.g. `arcadectl: set screensaver brightness 60`).
- Push model: any state change → commit → push rendered diff to cabinet over WS → cabd applies → acks. If cabinet offline, queued and replayed on reconnect.
- Jellyfin adapter: enumerate libraries/series/movies, mint transcoded stream URLs (480p cap) for the screensaver playlist.
- ROMDL adapter: see §6.4.
- Cabinet presence tracking + event log (game launched, screensaver on/off, disconnects) — queryable, so agents can answer "what's the cabinet doing right now?"
- The REST API is plain, tokenless-or-single-token HTTP — deliberately trivial for smart-home platforms (Home Assistant `rest_command`, Node-RED, cron) to call (AGT-5): e.g., `POST /screensaver/set {"brightness": 30}` at 10pm nightly.

### 6.2 `arcadectl` (CLI — the primary interface)

Global flags: `--json` on everything (AGT-1), `--cab <name>` (future-proofing for >1 cabinet, defaults to the only one). Command surface (v1):

```
arcadectl status                          # cabinet online? what's running? volumes? screensaver state?
arcadectl games list [--system X] [--favorites]
arcadectl games launch <query|id>         # fuzzy match; --exact for agents
arcadectl games kill                      # return to frontend
arcadectl favorites add|remove|list <query|id>

arcadectl volume get
arcadectl volume set --master 70
arcadectl volume set --class games 100 --class trailers mute

arcadectl screensaver status|on|off|toggle
arcadectl screensaver disable --for 2h
arcadectl screensaver set --brightness 60 --idle 300 --mode shuffle_episodes
arcadectl screensaver sources             # every series/movie w/ enabled flag
arcadectl screensaver enable-source|disable-source <query|jellyfin-id>

arcadectl controls list-presets
arcadectl controls save-preset <name> | load-preset <name> | diff <a> <b>
arcadectl controls map --scope global|system:<sys>|game:<id> --set a=b ...
arcadectl controls monitor                # stream live button presses (for testing mappings)

arcadectl roms search <query>             # via ROMDL
arcadectl roms request <query|id> [--system X]
arcadectl roms queue|status
arcadectl roms sync                       # force delivery/rescan

arcadectl stream apps                     # Sunshine app list
arcadectl stream launch <app>             # start Moonlight session on cabinet

arcadectl cab logs [-f] | screenshot | exec -- <cmd> | reboot | restart-frontend
arcadectl cab remote-view                 # on-demand VNC
arcadectl cab lifeline on|off
arcadectl state log|diff|rollback <commit>   # git history of all config
```

### 6.3 Web UI (AGT-3 — secondary priority)

Same REST API. Pages: dashboard (status, screenshot, quick actions: volume sliders per class, screensaver toggle+brightness, launch favorites); controller remapper (the flagship, §5.7); screensaver sources (checkbox list w/ thumbnails); ROM request queue; game library/favorites. Red/blue styling to match the cabinet. No auth beyond, at most, one shared password (prime directive 5).

### 6.4 ROM pipeline (ROM-1..3)

- Adapter around the existing **ROMDL** package (interface to be confirmed against its actual API — OQ-4; assumed: search(query) → candidates, download(id) → file(s)).
- Flow: `request` → queued in arcaded → ROMDL downloads to server staging → checksum → pushed to cabinet over the tunnel (HTTP resume-capable) into the right system folder → cabd triggers Pegasus library rescan → optional scrape of art/metadata → event "ROM ready: <name>" visible in `arcadectl roms status`.
- Fully agent-drivable: "get Metal Slug 3 on the cabinet" ≡ `arcadectl roms request "metal slug 3" && arcadectl roms sync`.

### 6.5 Skills & agent onboarding (AGT-1, AGT-2)

- `AGENTS.md` / `CLAUDE.md` at repo root: 30-second orientation — what the system is, where state lives, "always use arcadectl, never hand-edit rendered configs on the cabinet."
- `skills/*/SKILL.md`: one per domain (screensaver, audio, controllers, roms, games, diagnostics). Each contains: purpose, the relevant `arcadectl` commands with examples, the state files it touches, and a worked end-to-end example ("user says the trailers are blasting audio → check `arcadectl volume get` → ...").
- Everything returns `--json`; exit codes are meaningful; no interactive prompts anywhere in the CLI (agents hate prompts). **No MCP server, ever** (AGT-2).

---

## 7. Cabinet↔Server protocol (v1 sketch)

JSON messages over the WebSocket; request/response + server-push events.

```jsonc
{"id":"uuid","kind":"req","method":"game.launch","params":{"system":"snes","path":"..."}}
{"id":"uuid","kind":"res","ok":true,"result":{...}}
{"kind":"event","event":"heartbeat","state":{"running":"snes/smw.sfc","screensaver":false,"volumes":{...}}}
```

Method families: `game.*` (launch/kill/running), `screensaver.*` (on/off/set/playlist), `audio.*` (apply policy), `input.*` (press/monitor), `state.sync` (config bundle push), `file.*` (ROM/media delivery negotiation), `system.*` (exec/screenshot/reboot/logs/remote-view), `lifeline.*`. Idempotent where possible; every mutation acks with resulting state so the server's view is always truth-checked.

---

## 8. Milestones & acceptance criteria

Ordered so the sacred core lands first; each milestone is independently shippable and demo-able.

**M0 — Skeleton & link.** Debian image on cabinet; cabd + arcaded talking over the tunnel; `arcadectl status`, `cab exec`, `cab screenshot`, `cab reboot` work; auto-reconnect proven by yanking Wi-Fi for 10 min.
*Accept:* cabinet survives 24h with ≥99% heartbeat delivery; reconnects unaided from router reboot.

**M1 — Unshakeable emulation + frontend.** RetroArch + cores; Pegasus + red/blue theme; favorites-default view; favoriting on a button; HOME_COMBO; watchdog; config rendering from state repo.
*Accept:* EMU-1 bar (100 clean scripted launch/exit cycles per system); UI-4 navigation audit passes; kill -9 a running emulator → frontend focused within 2s.

**M2 — Control plane.** Audio classes + policy enforcement; volume via CLI; controller scopes + presets save/load via CLI; `controls monitor`; state git log/rollback.
*Accept:* trailer audio never audible with default policy (automated: play trailer, assert node muted); preset round-trip (save→scramble configs on purpose→load→verify launch renders clean).

**M3 — Screensaver.** Jellyfin integration; 480p transcode; panscan 4:3 crop; brightness tint live-adjustable; per-source enable/disable; idle in/out; force combo + CLI; offline fallback clips.
*Accept:* SCR-1..7 each demonstrated; measured stream ≤480p; zero letterbox/pillarbox pixels on the panel; brightness change applies <500ms without playback hiccup.

**M4 — ROM pipeline.** ROMDL adapter; request→deliver→rescan flow; `roms` CLI family.
*Accept:* `arcadectl roms request` for a known title results in the game launchable from the cabinet with art, no human touches.

**M5 — Streaming.** Sunshine/Apollo on desktop; Moonlight entries in Pegasus; Greenlight installed + signed in; both reachable as tiles.
*Accept:* SF6 playable end-to-end from cabinet controls at 60fps on LAN; HOME_COMBO exits stream cleanly; xCloud title playable (best-effort — document failure mode if Microsoft breaks it).

**M6 — Web UI + skills polish.** Dashboard, remapper UI, screensaver sources page, ROM queue; all SKILL.md files written and tested by actually pointing Claude Code at the repo cold.
*Accept:* a fresh agent given only the repo + "make the screensaver dimmer and disable Redline" completes it via CLI without human help.

**Deferred:** Bluetooth (CTL-5), multi-cabinet, security (permanently, per decree).

---

## 9. Open questions (blocking or shaping)

| ID | Question | Blocks |
|----|----------|--------|
| OQ-1 | Cabinet display native resolution/aspect (assumed 4:3 panel — what resolution?) | M3 crop math, theme layout |
| OQ-2 | Cabinet hardware specs (CPU/GPU/RAM/disk) — bounds emulator set & Greenlight (Electron) perf | M1, M5 |
| OQ-3 | Exact system list to emulate (and any current ROM library to migrate off Batocera, incl. saves) | M1 |
| OQ-4 | ROMDL's actual API surface (module? CLI? queue semantics?) | M4 |
| OQ-5 | Desktop OS (Windows assumed) for Sunshine vs Apollo choice | M5 |
| OQ-6 | Is there an Xbox console for home streaming, and on which network? | STR-3 |
| OQ-7 | Jellyfin already running, or standing it up (and is the library currently in Plex)? | M3 |
| OQ-8 | Server hardware/OS + available upstream bandwidth (sizes the 480p bitrate cap) | M3 |
| OQ-9 | Panel button/joystick inventory (how many buttons per player? admin buttons?) — needed for combos & remapper UI | M1, M2 |
| OQ-10 | Preferred implementation language for cabd/arcaded (Python vs Go) | M0 |

## 10. Explicitly out of scope

Security/auth hardening (owner decree); Bluetooth (v1); resume-position syncing for screensaver media; scraping infrastructure beyond ROMDL + art scrape; mobile apps; MCP (permanently).

## 11. Feasibility research notes

- **Xbox streaming on Linux:** viable today via Greenlight (open-source Electron client for xCloud + home streaming; actively maintained, v2.4.2 June 2026; Linux builds + Flathub). Unofficial → classified best-effort.
- **Steam LAN streaming:** Sunshine (LizardByte) or Apollo fork host + Moonlight-qt client is the standard, well-supported pair on Linux clients.
- **RetroArch remote control:** built-in network command interface (UDP) supports pause/quit/menu/etc.; remap files support global/core/game layering — matches the mapping model.
- **480p Jellyfin transcode, mpv panscan crop, PipeWire per-node volume:** all standard, low-risk.

