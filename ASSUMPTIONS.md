# ASSUMPTIONS

- **A-1 (env):** Loop prompt says "this box is Windows (WSL2)". The actual execution
  environment is a Linux container with native Docker 29.3.1. Proceeding with plain
  Linux Docker; the compose files remain WSL2-compatible (no privileged kernel features
  beyond what WSL2 Docker Desktop supports) so the rig still runs on the owner's box.
- **A-2 (language, OQ-10):** cabd/arcaded implemented in **Python** (FastAPI + typer +
  websockets) — faster iteration for an agent loop; spec allows Python or Go.
- **A-3 (display, OQ-1):** Cabinet panel assumed 4:3 at 1024×768 for sim (Xvfb geometry).
- **A-4 (systems, OQ-3):** Initial emulated system list: nes, snes, genesis, arcade
  (fbneo), gba, psx.
- **A-5 (ROMDL, OQ-4):** ROMDL interface assumed per spec: `search(query) → candidates`,
  `download(id) → file(s)`; sim uses a faked ROMDL backend honoring that contract.
- **A-6 (tunnel):** Cloudflare Tunnel is simulated by routing cabinet→server WebSocket
  through Toxiproxy; the reconnect/chaos semantics are what matter, not Cloudflare itself.
- **A-7 (accelerated time):** "24h soak" acceptance runs use accelerated heartbeats
  (e.g. 20× faster) with the ratio recorded in each accept target; an 8h wall-clock clean
  soak is still required before DONE.md per loop rules.
- **A-8 (fakes):** Only game/streaming binaries are faked (retroarch, moonlight,
  greenlight, steam, pegasus): scripts that open a real X window on Xvfb, hold it, exit
  on SIGTERM/UDP quit — so watchdog/focus logic is tested against real X behavior.
  mpv, PipeWire, Jellyfin, Xvfb are real.
- **A-9 (accept-m0 scaling):** Spec M0 accept = "24h with >=99% heartbeat delivery;
  reconnects unaided from router reboot". Read as two criteria: (1) sustained delivery
  >=99% measured over a 1200s wall-clock window at 0.25s beats (4800 beats, server-side
  delivered count) — chaos-free window since the real 24h bar's 1% budget maps to
  natural flakiness, and injected chaos is exercised separately (test-m0 + soak);
  (2) router reboot simulated as toxiproxy container restart (in-memory proxy config
  wiped = dead link) + proxy recreation, cabinet back unaided <=60s with a NEW WS
  connection and a working exec round-trip. 24h wall-clock validation on real
  hardware -> HARDWARE-TODO.md.
- **A-10 (accept-m1 scaling):** Spec play window is 5s per cycle; sim cycles use 0.3s
  (~17x, same acceleration convention as A-7/A-9) because the faked emulator has no
  play-time-dependent behavior — the cycle asserts launch, window, config cleanliness,
  kill, and focus recovery. Real-duration EMU-1 cycles on real cores are ledgered in
  HARDWARE-TODO.md.
- **A-11 (/statework ephemerality in sim):** The server's writable git state repo
  (/statework) is seeded from the RO-mounted repo state/ at container start and lives
  in the container fs — container recreation resets runtime mutations (e.g. favorites
  added during a test) and git history. Acceptable in sim (each accept run is
  self-contained); on real hardware the state repo is a persistent git repo on the
  server's disk, and seed-vs-work divergence doesn't apply.
- **A-12 (sim audio anchor):** PipeWire null sinks discard volume changes while
  SUSPENDED, so the sim cabinet keeps a silent anchor stream playing to hold the sink
  RUNNING. Real ALSA sinks persist volume in the device; still, verify master-volume
  behavior across sink suspension on real hardware (HARDWARE-TODO).
- **A-13 (sim input source):** `controls monitor` (input.monitor RPC) reads button
  events from a FIFO (/tmp/input-events) in sim — there are no evdev devices in a
  container. On real hardware cabd reads evdev (grabbing combos too); that path is in
  HARDWARE-TODO.md. The RPC surface and CLI are identical either way.
- **A-14 (accept-m2 mute-observation budget):** The spec's 100ms figure (§5.6) is the
  ENGINE's reaction budget, verified by direct event-timestamp measurement at 17-27ms
  (journal iter 20). accept-m2's external observation loop allows 500ms from
  first-unmuted-observation to muted, covering pactl-subprocess polling overhead —
  an observation allowance, not an engine budget. The 5s reconcile backstop cannot
  satisfy the 500ms leg, so acceptance still proves the event path.
- **A-15 (Jellyfin auth, OQ-7):** In sim, arcaded bootstraps a fresh Jellyfin (wizard +
  user arcade/arcade + libraries). On a REAL pre-configured Jellyfin, set
  JELLYFIN_TOKEN (API key) — bootstrap is skipped entirely. Without a token and
  without an arcade/arcade user, screensaver endpoints fail loudly (500 w/ 401
  upstream) until one of the two is provided.
- **A-16 (soak continuity vs host suspension):** The dev environment's VM is reclaimed
  on inactivity and can pause/restart the soak containers mid-window (observed
  2026-07-23: clean soak killed at 08:07Z by a host restart, resumed 14:05Z). A host
  suspension is not a product violation, but per the loop rules the 8h window RESTARTS
  after each one (new epoch from soak READY). If repeated suspensions make one
  continuous 8h window unattainable in this environment, the fallback — declared here
  in advance, not after the fact — is 8h of CUMULATIVE clean soak across
  suspension-bounded segments, each segment individually violation-free, all segments
  journaled. Real-hardware DONE criteria remain a continuous window.
- **A-17 (no uinput in dev kernel):** The dev host kernel has CONFIG_INPUT_UINPUT
  unset, so virtual evdev devices cannot be created in sim. The production input
  path is still real: cabinet/inputs.py ships the evdev reader (device enumeration,
  BTN_* mapping) and the ComboEngine used by BOTH evdev and the sim FIFO source; the
  engine's hold/latch/multi-button semantics are unit-tested (tests/) and exercised
  end-to-end in sim via timed FIFO down/up lines. Only physical device enumeration
  remains hardware-scope.
- **A-18 (real Pegasus binary unavailable in dev env):** pegasus-frontend isn't in
  Debian and its GitHub releases are unreachable from this session (repo-scoped
  proxy blocks api/github downloads — verified). The theme is validated with Qt6
  qmllint (0 errors; caught a missing QtMultimedia import) and all generated
  collection metadata is asserted by test-pegasus-meta; the live render + UI-4
  navigation audit remain HARDWARE-TODO items against real Pegasus.

## A-19 — Provisioning validated statically, not by booting a kiosk
This environment cannot reboot a machine into an X11/Openbox kiosk with
systemd user sessions. cabinet/provision + server/provision are therefore
validated by: shellcheck + bash -n (install scripts), systemd-analyze verify
with stubbed ExecStart binaries in a debian:trixie container (unit
syntax/lint), udevadm verify (rules), and placeholder-substitution checks
(make test-provision). Real first-boot on the cabinet is HARDWARE-TODO.
Design decision recorded: Pegasus has ONE supervisor — pegasus.service
(Restart=always); cabd's spawn loop is disabled via empty FRONTEND_BIN
(watchdog still refocuses by window title). The spec lists both a
pegasus.service and "cabd restarts Pegasus"; dueling supervisors would race
to double-spawn, so systemd owns the process and cabd owns focus/health.

## A-20 — v1 scope deferrals (SUPERSEDED by P8, iteration 54)
The items below were deferred at the first DONE v2, then implemented in P8
because they are software, not hardware, and the mandate is the whole spec:
- Per-game mapping scope `game:<sys>/<rom>` (§5.7 layer 3) — IMPLEMENTED
  (controllers/games.yaml, merged over the system layer at render time).
- CTL-3 interactive web remapper (press physical button → click logical
  target, live evdev relay) — IMPLEMENTED (webui capture()/assign()).
- `input.press` / `input.text` RPCs (§5.10) — IMPLEMENTED (logical combos
  fire the same handlers as the panel; other keys go to xdotool).
- Lifeline recovery paths (§5.11: ensure SSH listening, fallback transport,
  restart networking on a panel button) — IMPLEMENTED, commands overridable
  per environment (LIFELINE_SSH_CMD / _TRANSPORT_CMD / _NET_RESTART_CMD);
  the sim proves each by its observable effect (a real :22 listener, marker
  files), and the real systemd/NetworkManager/tailscale commands are the
  hardware defaults (A-19 scope).
- Web UI library page + launch/favorite quick actions (§6.3) — IMPLEMENTED.
Still deferred (unchanged): Bluetooth controllers (CTL-5, spec says out of
scope for v1); box-art thumbnails in the web UI (art is delivered to the
cabinet and shown by Pegasus; the web UI lists titles).

## A-21 — Sim tests must run SERIALLY (one make target at a time)
All test targets drive the same `arcade-sim` compose project: they
`up --force-recreate` containers, mutate the shared state repo, and launch
real mpv/RetroArch processes inside one cabinet container. Two suites (or a
verifier subagent and the main loop) running at once recreate each other's
containers mid-assertion and stack real AV processes until cabd is OOM-killed
and respawns — producing failures like "service cabinet is not running" or a
stale render, which do not reproduce serially (test-m2 verified 5/5 clean
alone, 2 failures in 5 under concurrency). Verified root cause, not a product
defect. Run one target at a time; give a verifier subagent exclusive use of
the sim while it works.
