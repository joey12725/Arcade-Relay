# STATE

**Updated:** 2026-07-23 iteration 18

## Current position
- Milestone: **M-SIM** (simulation rig — prerequisite before M0)
- Current unit: M2 unit 1 — audio policy engine (real PipeWire in cabinet
  container: null sink, pactl-based class enforcement, media.role tagging at spawn,
  <=1s reaction to new nodes; state/audio.yaml; arcadectl volume get/set).
  Then: presets save/load + controls monitor + state rollback; accept-m2.
- Iter 12 done: verifier FAIL (supervisor died on cabd crash) -> fixed supervisor,
  kill_strays convergence, frontend pdeathsig, watchdog identity check under lock,
  window-teardown wait; test-m1 step 7 added (kill -9 cabd -> converge). 7/7 green.
- Next units (M1 remainder): config rendering from state repo before every launch
  (retroarch.cfg + remaps from state/, scramble->relaunch->clean test); favorites
  (state/library/favorites.yaml + arcadectl favorites); then accept-m1 (100
  launch/exit cycles x 6 systems, kill -9 recovery <2s, zero drift) x3.
- Real-Pegasus QML theme + UI-4 navigation audit -> HARDWARE-TODO (fake frontend in sim).
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
| M0 | **DONE** (3/3 accept passes: 1.0004/1.0004/1.0006) | 3/3 |
| M1 | **DONE** (3/3 on strengthened harness; 3600 clean cycles total) | 3/3 |
| M2 | starting: audio policy engine | 0/3 |
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
