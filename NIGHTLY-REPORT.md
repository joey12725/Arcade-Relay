# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 27

## TL;DR
M0/M1/M2 CERTIFIED (3/3 each). M3 feature-complete, acceptance chain running: real Jellyfin 480p transcodes streamed through the chaos link into mpv (panscan crop proven from render margins, brightness tint verified by screenshot luma), idle in/out, offline local-clip fallback (proven with the link cut), screensaver crash self-heal, disable --for. Soak refreshed onto current product images after finding it ran stale ones; auditor mid-write read race fixed.

## Milestones
- M-SIM: DONE (rig + soak, subagent-verified)
- M0/M1/M2: DONE (3/3 each)
- M3: feature-complete; accept chain executing
- M4 (ROM pipeline): next
- M0–M6: not started

## Soak
RUNNING on REAL daemons (arcaded/cabd) since iter 6: poller samples server view 2x/s; V5 rule active. Last audit SOAK CLEAN (4 partitions, 3 cabd kill -9s, 8 reconnects).

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
