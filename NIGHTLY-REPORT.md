# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 2

## TL;DR
Sim rig up and green: 4-container Docker stack (toxiproxy-isolated cabinet with real Xvfb/mpv/PipeWire, real Jellyfin). `make sim-test` passes with chaos assertions.
Next: soak stack (chaos driver, `make soak-up`/`soak-status`), then M0.

## Milestones
- M-SIM: in progress (1 unit done: rig skeleton green; soak stack remains)
- M0–M6: not started

## Soak
Not running yet — next unit.

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
