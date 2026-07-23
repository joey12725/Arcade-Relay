# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 11b

## TL;DR
M0 CERTIFIED: accept-m0 passed 3/3 consecutive (delivery ratios 1.0004/1.0004/1.0006 + router-reboot recovery each run). M1 lifecycle live end-to-end: arcadectl games launch/kill -> cabd -> launcher; watchdog recovers kill -9 (668ms external clock) AND hangs; cabd supervises the frontend. TEST-M1/M0/SIM + soak all green. Remaining M1: config rendering from state repo, favorites, accept-m1 x3.

## Milestones
- M-SIM: DONE (rig + soak, subagent-verified)
- M0: DONE (3/3 certified)
- M1: lifecycle integrated (test-m1 6/6); config rendering + favorites + accept-m1 remain
- M0–M6: not started

## Soak
RUNNING on REAL daemons (arcaded/cabd) since iter 6: poller samples server view 2x/s; V5 rule active. Last audit SOAK CLEAN (4 partitions, 3 cabd kill -9s, 8 reconnects).

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
