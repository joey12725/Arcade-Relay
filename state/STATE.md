# STATE

**Updated:** 2026-07-23 iteration 2

## Current position
- Milestone: **M-SIM** (simulation rig — prerequisite before M0)
- Current unit: soak stack (`make soak-up`/`soak-status`) — next iteration
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
- NOT RUNNING yet — comes up with M-SIM. Until then, soak check each iteration is a no-op.

## Milestone status
| Milestone | Status | accept passes |
|-----------|--------|---------------|
| M-SIM | in progress | – |
| M0–M6 | not started | 0/3 each |

## Done units
1. (iter 1) Repo bootstrap: spec copied in, ROADMAP/STATE/JOURNAL/ASSUMPTIONS/
   HARDWARE-TODO/NIGHTLY-REPORT created, committed, pushed.
2. (iter 2) Sim rig skeleton: compose (toxiproxy/server/jellyfin/cabinet), cabinet
   image (real Xvfb/mpv/PipeWire), sim-test smoke (isolation + chaos + AV asserts),
   Makefile. Subagent-verified, findings applied, green twice.

## Next unit
Soak stack: `sim/soak/` — detached compose copy with a chaos driver container doing
random partitions (toxiproxy toggle) and kill -9s of cabinet processes on an
accelerated heartbeat cycle; `make soak-up`, `make soak-down`, `make soak-status`
(reports violations, exit non-zero if any). Placeholder heartbeat until cabd exists
(M0), then soak drives the real daemons.
