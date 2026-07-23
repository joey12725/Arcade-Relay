# STATE

**Updated:** 2026-07-23 iteration 1

## Current position
- Milestone: **M-SIM** (simulation rig — prerequisite before M0)
- Current unit: sim rig compose skeleton (next iteration)
- Consecutive failures on current unit: 0

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

## Next unit
Write `sim/docker-compose.yml` skeleton (server, cabinet, jellyfin, toxiproxy) +
`Makefile` targets `sim-up`/`sim-down`/`sim-test`; smoke test = all containers report
healthy and toxiproxy can create a proxy between cabinet and server.
