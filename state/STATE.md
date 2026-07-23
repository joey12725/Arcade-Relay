# STATE

**Updated:** 2026-07-23 iteration 10

## Current position
- Milestone: **M-SIM** (simulation rig — prerequisite before M0)
- Current unit: accept-m0 3-run chain executing (bm3qlf3ge, ~40min left) on
  drift-fixed cabd. While it runs: M1 units that don't touch loaded files.
  Next M1 unit: cabd integration of launcher (game.launch/game.kill RPCs +
  arcadectl games launch/kill) — AFTER the accept chain finishes.
  CONSTRAINT while chain runs: exec-only on arcade-sim; don't edit cabd.py/server//sim configs.
- Consecutive failures on current unit: 0
- NOTE: loop is now self-paced (user request: continuous, iterations chained
  back-to-back; 60s wakeup between turns — scheduler minimum).
- NOTE: Docker daemon must be started manually in this container (`dockerd &`);
  check `docker info` at iteration start.

## Environment facts
- Dev box: Linux container (not Windows/WSL2 as loop prompt assumed) with native
  Docker 29.3.1 + Compose v5.1.1. See ASSUMPTIONS.md A-1.
- Repo: joey12725/Arcade-Relay, branch `claude/arcade-cabinet-impl-nn0cka` (push target).
- Spec: `arcade-cabinet-spec.md` at repo root (copied from upload, v0.1 2026-07-22).

## Soak stack
- RUNNING (project arcade-soak, since iter 3). Check first each iteration:
  `make soak-status` must exit 0; any violation preempts the roadmap.

## Milestone status
| Milestone | Status | accept passes |
|-----------|--------|---------------|
| M-SIM | **DONE** (rig + soak verified; V1 auditor bug found & fixed) | – |
| M0 | acceptance chain running (run 1 pre-fix passed 0.9958; series restarted on drift-fixed cabd) | 0/3 |
| M1 | early units done: fakes + launcher/watchdog (verified) | 0/3 |
| M1–M6 | not started | 0/3 each |

## Done units
1. (iter 1) Repo bootstrap: spec copied in, ROADMAP/STATE/JOURNAL/ASSUMPTIONS/
   HARDWARE-TODO/NIGHTLY-REPORT created, committed, pushed.
2. (iter 2) Sim rig skeleton: compose (toxiproxy/server/jellyfin/cabinet), cabinet
   image (real Xvfb/mpv/PipeWire), sim-test smoke (isolation + chaos + AV asserts),
   Makefile. Subagent-verified, findings applied, green twice.
3. (iter 3) Soak stack: arcade-soak compose (chaos driver: partitions + kill -9s),
   supervised 4Hz heartbeat, soak-status violation audit. 120s run SOAK CLEAN.

4. (iter 4) M0 skeleton: arcaded (WS hub/REST/RPC) + cabd (heartbeats, jittered
   backoff, system.exec) + arcadectl (status, cab exec). TEST-M0 + SIM-TEST pass.

## Next unit
`make accept-m0`: scripted acceptance — accelerated 24h-equivalent run asserting
>=99% heartbeat delivery under chaos + reconnect after full proxy restart ("router
reboot"); needs a heartbeat-delivery counter on arcaded (/status connects/beats).
Also: migrate soak stack cabinet to run real cabd instead of heartbeat.sh.
