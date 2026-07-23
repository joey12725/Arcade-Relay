# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 29

## TL;DR
M0-M4 ALL CERTIFIED (3/3 acceptance each). M3: real Jellyfin 480p over the chaos link into mpv, photon-verified brightness, offline clip fallback + crash self-heal (proven with link cut). M4: 'arcadectl roms request "metal slug 3"' → checksum-verified ROM + art on the cabinet + rescan, launchable — no human touches (fake ROMDL backend behind the A-5 contract).

## Milestones
- M-SIM: DONE (rig + soak, subagent-verified)
- M0/M1/M2/M3/M4: DONE (3/3 each)
- M5 (streaming tiles): next; then M6 (web UI + skills)
- M0–M6: not started

## Soak
RUNNING on REAL daemons (arcaded/cabd) since iter 6: poller samples server view 2x/s; V5 rule active. Last audit SOAK CLEAN (4 partitions, 3 cabd kill -9s, 8 reconnects).

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
