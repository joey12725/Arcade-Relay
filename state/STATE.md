# STATE

**Updated:** 2026-07-23 iteration 3b

## Current position
- Milestone: **M-SIM** (simulation rig — prerequisite before M0)
- Current unit: M0 skeleton (arcaded + cabd + WS link) — next iteration
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
| M0–M6 | not started | 0/3 each |

## Done units
1. (iter 1) Repo bootstrap: spec copied in, ROADMAP/STATE/JOURNAL/ASSUMPTIONS/
   HARDWARE-TODO/NIGHTLY-REPORT created, committed, pushed.
2. (iter 2) Sim rig skeleton: compose (toxiproxy/server/jellyfin/cabinet), cabinet
   image (real Xvfb/mpv/PipeWire), sim-test smoke (isolation + chaos + AV asserts),
   Makefile. Subagent-verified, findings applied, green twice.
3. (iter 3) Soak stack: arcade-soak compose (chaos driver: partitions + kill -9s),
   supervised 4Hz heartbeat, soak-status violation audit. 120s run SOAK CLEAN.

## Next unit
M0 skeleton: `server/` (arcaded: FastAPI + WS hub + heartbeat tracking) and
`cabinet/` (cabd: outbound WS through toxiproxy, 5s heartbeats accelerated in sim,
exponential backoff reconnect capped 10s jittered), replacing the placeholder
python http.server in both sim and soak stacks; `arcadectl status` first command.
