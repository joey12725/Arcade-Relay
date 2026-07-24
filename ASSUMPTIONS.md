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
