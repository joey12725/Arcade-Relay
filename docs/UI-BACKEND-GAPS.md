# UI → backend gaps

The Y2K design (`Retro arcade cabinet interface` handoff) describes more than
the server currently implements. Everything in it is now either **wired to a
real endpoint** or **visibly marked inert in the UI itself** — there are no
buttons that silently do nothing.

This is the list of what is still missing, why, and what building it needs.
Ordered by how much work each one is.

---

## 1. Game library: load / unload and the transfer queue

**Design.** The server is a big library; the cabinet is a small cache. Each
row has LOAD / UNLOAD / CANCEL / RETRY, a per-transfer progress bar with
speed and ETA, a five-state badge (installed / not on cabinet / loading /
unloading / failed), and a TRANSFER QUEUE panel underneath. Free-space is
checked before a transfer starts.

**Why there is no backend.** There is no server-side game store at all. The
only library that exists is the cabinet's own `/roms` tree, read through the
`library.list` RPC; `_game_index()` in `arcaded.py` is a view of the cabinet,
not of the server. "Unload from cabinet, keep the server copy" has no server
copy to keep. The ROM queue (`/roms/request`) is a *different* thing — a
one-way acquire-and-deliver pipeline, with no notion of a game that lives on
the server and is not currently on the cabinet.

**What it needs.**
- A server-side library root (`/library/<system>/<rom>`) plus a manifest in
  the state repo recording size, checksum and provenance per game.
- Delivery reusing the existing `file.fetch` RPC (which already streams to
  `.part`, verifies sha256 and renames atomically) — but with progress. Right
  now `file.fetch` returns only on completion, so the cabinet must start
  emitting progress events (`file.progress`) for a bar to be possible.
- An unload RPC (`library.remove`) with a guard against removing the running
  game, plus a `library.rescan` afterwards.
- A persistent job table on the server (the ROM queue is an in-memory list
  that dies with the process — fine for a handful of downloads, not for
  something the UI treats as a work log).
- Free-space pre-check via `system.health`'s `disk_free_h`.

**Currently in the UI.** The library table is read-only plus LAUNCH and
FAV/UNFAV, both of which work. The TRANSFER QUEUE panel states this.

---

## 2. Controller combos and hold-to-trigger — BUILT

Combos and hold durations are implemented, but not where you might expect.

A mapping layer's flat `{physical: logical}` map renders into `retroarch.cfg`
/ `.rmp` and is applied by RetroArch. RetroArch's remap format cannot express
"these two buttons, held for two seconds", so combo bindings live in a
separate `bindings:` list on the same layer and **cabd** detects them
(`inputs.BindingEngine`, unit-tested in `tests/test_binding_engine.py`).

```yaml
# controllers/global.yaml
buttons: {a: a}              # flat remap, applied by RetroArch
bindings:                    # combos/holds, detected by cabd
  - {buttons: [l, r], hold_ms: 2000, target: start}
  - {buttons: [select], hold_ms: 0, target: screensaver}
```

Targets come in two kinds:

- **action** (`screensaver`, `save_state`, `quit`, `pause`, `reset`, …) —
  performed by cabd, some through RetroArch's network command port. Works
  everywhere.
- **button** (`a`, `start`, …) — emitted through a **uinput virtual pad**,
  because the emulator reads the physical pad directly and there is no way to
  tell it "press B" otherwise. Needs `/dev/uinput`; a container does not have
  it (A-17), and cabd logs the reason rather than failing silently.

Longest match wins, so binding `l+r` does not also fire the binding on `l`.

`arcadectl controls bind --scope global --buttons l+r --hold 2s --target start`

**Still open:** the three cabinet hotkeys (home / screensaver / lifeline) are
a separate mechanism in `cabinet.yaml` and are still not editable from the
remapper screen.

---

## 3. CPU / GPU / memory gauges and percentiles — BUILT

`cabinet/metrics.py` samples at 1 Hz into a 30-minute ring buffer and
`system.health` carries the summary. Percentiles are computed cabinet-side —
1800 samples x 8 metrics per dashboard poll is not a thing to put on the
tunnel.

Sources: `/proc/stat` deltas (CPU), `/proc/meminfo` (memory + swap, from
MemAvailable so the page cache is not counted as used), amdgpu's
`gpu_busy_percent` and `mem_info_vram_*` (GPU + VRAM), thermal zones with a
preference for a CPU/package sensor over the first zone, and amdgpu hwmon for
GPU edge temperature.

Every reader returns None when its node is missing, so the same code runs on
the cabinet, in the container sim and on a dev box. The sysfs roots are
injectable, which is how `tests/test_metrics.py` covers the Linux-only paths
against fixture trees.

**Still open:** GPU readers are amdgpu-specific — an Nvidia or Intel cabinet
would need its own reader (`nvidia-smi` / i915 sysfs).

---

## 4. Elapsed play time — BUILT

cabd stamps a launch time on every session (monotonic for the counter,
immune to NTP steps; epoch for display) and the heartbeat carries `system`,
`started_at` and `elapsed_s`. The dashboard interpolates between heartbeats
so the counter ticks once a second rather than jumping every five.

It also feeds the Discord "played for" line and the Home Assistant
`media_position` attribute.

---

## 5. Per-game streaming quality overrides

**Design.** A QUALITY dropdown per game (`inherit app default` /
`720p60 · 12000 kbps` / …).

**Why there is no backend.** `/stream/games/set` accepts and stores a
`quality` block per game, and it is committed to `streaming.yaml` — but
nothing reads it. `cabd-launch stream …` applies the **app-level** quality.

**What it needs.** The stream launcher resolving quality as
game-override → app-override → defaults, which means the per-game launch
target rendered by `pegasus_gen.gen_streaming` has to carry (or be able to
look up) its own quality. Small, but it touches the launch path.

**Currently in the UI.** The ON CAB toggle, add and remove all work and do
reach the cabinet's Streaming collection. The quality dropdown is omitted
with a note rather than shown as a working control.

---

## 6. "Disable until reboot" screensaver suppression

**Design.** Suppress options of 30m / 2h / 8h / **until reboot**.

**Why there is no backend.** `screensaver.disable` takes `seconds` and sets
`suppress_until = monotonic() + secs`. There is no sentinel for "indefinite",
and monotonic time does not survive a reboot anyway (which, for once, means
the *default* behaviour is already "until reboot" — but only by accident, and
there is no way to ask for it).

**What it needs.** Accept `seconds: null`/`0` as indefinite with an explicit
flag, and surface the suppression state in the heartbeat so the UI can show
it is active. The 30m / 2h / 8h buttons and RESUME all work today.

---

## 7. ROM request system picker — BUILT (and now required)

`server/romdl.py` drives the real ROMDL CLI
(example.invalid/romdl) over its documented `--json` interface. That
searches one console at a time, so the system is now a **required** parameter
rather than the nice-to-have the design implied, and the ROM tab has the
dropdown.

**But read this before using it:** ROMDL's Mirror source — its only source
for cartridge consoles — **shut down on 31 March 2026**. `mirror.invalid`
serves a closure notice instead of a directory index, so its crawler finds
zero directories and every non-PlayStation search returns nothing. Only
PkgIndex (ps1 / psp / ps3 / vita) still works.

The adapter names this rather than letting it look like a bad query: the ROM
tab and `arcadectl roms search` report "ROMDL has no live source for this
console", and `arcadectl roms backend` lists coverage per system. Restoring
cartridge-console support means adding a source to the ROMDL repo.

A second ROMDL quirk is worked around here: its PkgIndex source falls
back to querying *every* PlayStation database when the console name matches
none of its aliases, so a `snes` search returned PS Vita titles that outranked
everything on score. The adapter drops PkgIndex results for consoles it
does not actually cover.

---

## 8. Alerts as first-class records

**Design.** An ALERTS & ERRORS panel with WARN / ERROR / INFO severities and
timestamps.

**What exists.** `/alerts` classifies the free-text presence log by keyword
and folds in ROM-queue errors. It is genuinely useful but it is a heuristic —
a handler that logs "screensaver source removed" is INFO by luck, not by
declaration.

**What it needs.** `hub.log_event` taking an explicit severity, and callers
passing one. Mechanical, ~40 call sites.

---

## 9. Controller device picker is display-only

`/controls/devices` really does enumerate the cabinet's evdev devices, and
the dropdown shows them. But `input.monitor` listens on *all* devices, so
selecting one does not scope the capture. Needs a `device` parameter on
`input.monitor` and on `EvdevSource.open_devices`.

---

## Not gaps — deliberate, and worth not "fixing"

- **Trailer and screensaver volume are policy-only.** The sliders store a
  level; the cabinet keeps both muted (AUD-2). This is spec'd behaviour and
  the audio tab says so.
- **Pegasus's own favourite flag diverges from server favourites.** The
  theme's FAV button toggles Pegasus's internal star; the authoritative list
  is `state/library/favorites.yaml`, and the generated Favorites collection
  follows *that*. Pre-existing, tracked in HARDWARE-TODO.md.
- **No CRT scanlines / curvature / bloom.** Explicitly excluded by the design
  brief, and not implemented.

---

## Box art

Implemented, not a gap — but worth knowing how it behaves.

`server/artdb.py` resolves art from **libretro-thumbnails** (no API key, no
account; keyed on No-Intro / Redump ROM names, which is what `/roms` holds)
and from **Steam's CDN** for Steam entries by appid. Three tiers: exact
sanitised name, then name variants (article swaps, tag stripping, common
region suffixes), then a cached per-system directory index for a loose match.

- `POST /art/warm` fills the server-side cache for the whole library.
- `POST /art/push` delivers cached art to `/roms/art/<rom>.png`, which
  `pegasus_gen` attaches as `assets.boxFront`.
- Misses are cached too (with a TTL) so a cold library does not re-hammer the
  CDN on every page load.

Streaming entries have no art source — those tiles use the theme's generated
cover. Adding art there means a per-entry image URL in `streaming.yaml`.

---

## Integrations: Discord and Home Assistant

Implemented (`server/integrations.py`, BROADCAST tab). Both consume one
now-playing snapshot driven off the heartbeat, so neither polls the cabinet.

**Discord** posts an embed to a channel webhook when a game starts and/or
stops: title, system, play time, box-art thumbnail. It is a webhook and not
Rich Presence on purpose — Rich Presence is delivered by a *local desktop
Discord client* over an IPC socket, and the cabinet does not run one.

Posts fire on state *transitions*, not per heartbeat. The final play time is
read from the last heartbeat that still had a game running, not from the last
state change (which is pinned at launch and would report a couple of seconds).

**Home Assistant** needs two transports, because neither alone is enough:

- **MQTT discovery** for a screensaver switch, a **Screensaver playlist**
  select (options: `All shows` + every playlist; the option list is
  re-announced when playlists are added or removed; commands arrive on
  `.../playlist/set`), a stop-game button, a
  connectivity binary sensor, and sensors for title, system, play time, CPU,
  GPU, memory, CPU temperature and disk. Real entities, controllable, retained
  so they survive an HA restart, with availability on an MQTT last-will.
  Commands come back on `arcade/<node>/command` and `.../screensaver/set`.
- **REST** (`/api/states`) for the `media_player` entity. HA's MQTT
  integration has **no media_player platform** — a
  `homeassistant/media_player/.../config` topic is accepted by the broker and
  then silently ignored, which looks like it worked until you go looking for
  the entity. Needs an HA URL and a long-lived token.

Two honest limits on the REST media player, stated in the UI: HA cannot call
services on a REST-pushed entity (control comes from the MQTT switch/button),
and its state is dropped on an HA restart — arcaded re-pushes every heartbeat,
so it returns within seconds.

Box art in both needs `public_base_url` set to something the consumer can
reach: the public internet for Discord embed thumbnails, the LAN for HA.

`python3-paho-mqtt` is shipped in the server image; without it the MQTT half
logs and stays off rather than failing.
