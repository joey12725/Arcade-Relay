# NIGHTLY REPORT

**As of:** 2026-07-23, iteration 4b

## TL;DR
M0 skeleton LIVE and verified: real arcaded (WS hub + REST) + cabd (heartbeats, jittered backoff, exec RPC) + arcadectl over the toxiproxy link. test-m0 covers partition, hang (socket-open/heartbeats-blocked), reconnect, exec round-trip. Verifier PASS; 4 hardening fixes applied.
Next: `make accept-m0` (accelerated 24h-equivalent, >=99% heartbeat delivery, router-reboot recovery), then soak migrates to real cabd.

## Milestones
- M-SIM: DONE (rig + soak, subagent-verified)
- M0: skeleton done + verified; acceptance runs pending (0/3)
- M0–M6: not started

## Soak
RUNNING (arcade-soak): last audit SOAK CLEAN — 1319 heartbeats, 84% OK (all FAILs inside chaos windows), 15 partitions, 9 kill -9s, zero violations.

## Blockers / risks
None. Docker + Compose confirmed working in this environment.

## Assumptions made today
A-1..A-8 (see ASSUMPTIONS.md) — notably: Python for daemons, 1024×768 sim display,
Toxiproxy stands in for the Cloudflare Tunnel.
