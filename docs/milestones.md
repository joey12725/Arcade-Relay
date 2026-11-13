# Milestones

Derived from `docs/spec.md` §8. Milestones are worked strictly in order.
A milestone is DONE only when its acceptance criteria run as `make accept-mN` and pass
**3 consecutive times**.

## M-SIM — Simulation rig (prerequisite, not in spec)

Target hardware is Linux; development happens in Docker. Before any feature work:

- [ ] `docker-compose.sim.yml`: `server` container (arcaded), `cabinet` container
      (Xvfb + real mpv + real PipeWire), real `jellyfin` container, `toxiproxy` container.
- [ ] Cabinet ↔ server traffic flows **only** through Toxiproxy so partitions/latency/
      bandwidth chaos are injectable.
- [ ] Fake binaries only for game/streaming programs (retroarch, moonlight, greenlight,
      steam) — small scripts that open an X window, hold it, respond to signals/UDP.
      Everything else (mpv, PipeWire, Jellyfin, Xvfb) is real.
- [ ] `make sim-up`, `make sim-down`, `make sim-test` (smoke: all containers healthy,
      chaos injection works).
- [ ] Soak stack: `make soak-up` runs a detached copy with accelerated heartbeats and a
      chaos driver doing random partitions and `kill -9`s; `make soak-status` reports
      violations. Checked first every iteration.

## M0 — Skeleton & link

cabd + arcaded talking over WebSocket (tunnel simulated by Toxiproxy); `arcadectl status`,
`cab exec`, `cab screenshot`, `cab reboot`; auto-reconnect (exp backoff capped ~10s,
jittered, forever); heartbeat 5s / offline after 3 missed.

*Accept (`make accept-m0`):* accelerated-time equivalent of 24h with ≥99% heartbeat
delivery under chaos; cabinet reconnects unaided after a 10-min (accelerated) partition
and after a full "router reboot" (proxy restart).

## M1 — Unshakeable emulation + frontend

RetroArch (faked binary in sim) + Pegasus (faked) launch path via cabd; favorites-default
view; HOME_COMBO; watchdog (game dies → frontend focused <2s); config rendering from
state repo before every launch.

*Accept (`make accept-m1`):* 100 clean scripted launch/exit cycles per system, zero
hangs/drift/input loss; kill -9 a running "emulator" → frontend focused within 2s.
Real-hardware parts (real RetroArch behavior, real panel input) → docs/hardware-checklist.md.

## M2 — Control plane

Audio classes + policy enforcement on real PipeWire in the cabinet container; volume CLI;
controller scopes + presets save/load; `controls monitor`; state git log/rollback.

*Accept (`make accept-m2`):* trailer-class node asserted muted while playing; preset
round-trip (save → scramble rendered configs → load → next launch renders clean).

## M3 — Screensaver

Real Jellyfin integration; 480p transcode cap; mpv `--panscan=1.0` crop; brightness tint
via IPC <500ms without hiccup; per-source enable/disable; idle in/out; force CLI; offline
fallback clips.

*Accept (`make accept-m3`):* SCR-1..7 demonstrated in sim; measured stream ≤480p;
brightness change <500ms. Physical 4:3 panel verification → docs/hardware-checklist.md.

## M4 — ROM pipeline

ROM delivery adapter (fake backend in sim; interface per spec assumption OQ-4);
request→download→deliver→rescan; `roms` CLI family.

*Accept (`make accept-m4`):* `arcadectl roms request` for a known title → launchable
from cabinet library, no human touch.

## M5 — Streaming

Moonlight/Greenlight entries (faked binaries); launch/kill via cabd; HOME_COMBO exits
stream cleanly.

*Accept (`make accept-m5`):* stream tile launch→HOME_COMBO exit clean in sim. Real
SF6@60fps LAN + xCloud → docs/hardware-checklist.md.

## M6 — Web UI + skills polish

Dashboard, remapper UI, screensaver sources page, ROM queue; all SKILL.md files.

*Accept (`make accept-m6`):* fresh agent given only the repo + "make the screensaver
dimmer and disable Redline" completes it via CLI without human help.

## Exit

All milestones green ×3 + 8h clean soak → docs/acceptance.md + final report.
