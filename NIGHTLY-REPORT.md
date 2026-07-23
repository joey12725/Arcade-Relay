# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 22

## TL;DR
M0, M1, M2 all CERTIFIED (3/3 acceptance passes each). M2's acceptance itself caught a real reliability bug: wireplumber can lose the container-start race and die silently, making all volume/mute enforcement a no-op — audio daemons are now supervised (Restart=always) and the healthcheck asserts session-manager liveness. Full control plane shipped: audio classes on real PipeWire, mapping CLI, presets, state git log/rollback, controls monitor.

## Milestones
- M-SIM: DONE (rig + soak, subagent-verified)
- M0/M1/M2: DONE (3/3 each)
- M3 (screensaver): next — Jellyfin, 480p, panscan, brightness IPC
- M0–M6: not started

## Soak
RUNNING on REAL daemons (arcaded/cabd) since iter 6: poller samples server view 2x/s; V5 rule active. Last audit SOAK CLEAN (4 partitions, 3 cabd kill -9s, 8 reconnects).

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
