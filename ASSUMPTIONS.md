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
