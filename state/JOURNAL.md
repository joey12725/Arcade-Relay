# JOURNAL

Append-only. One entry per iteration, with evidence (commands + exit codes).

---

## Iteration 1 — 2026-07-23 — Repo bootstrap

**Unit:** First-run bootstrap: copy spec into repo, create ROADMAP.md, state/STATE.md,
state/JOURNAL.md, ASSUMPTIONS.md, HARDWARE-TODO.md, NIGHTLY-REPORT.md, .gitignore.

**Soak check:** N/A — soak stack not built yet (M-SIM pending).

**Evidence:**
- `docker --version` → `Docker version 29.3.1, build c2be9cc` (exit 0)
- `docker compose version` → `v5.1.1` (exit 0)
- `uname -a` → Linux x86_64 (exit 0) — dev box is Linux, not Windows; recorded A-1.
- `cp <upload> arcade-cabinet-spec.md` → exit 0
- Repo had no commits (`git log` exit 128, "does not have any commits yet") — confirmed
  true first run on branch `claude/arcade-cabinet-impl-nn0cka`.
- Commit + push evidence appended below by the commit step.

**Verification:** Docs-only unit — no tests to run; subagent verification starts with the
first code unit (sim rig skeleton, iteration 2).

**Outcome:** GREEN (bootstrap complete).

**Commit:** 4081676 "Bootstrap: spec, roadmap, state tracking, assumptions" — pushed to
origin/claude/arcade-cabinet-impl-nn0cka (exit 0, new branch created).

---

## Iteration 2 — 2026-07-23 — M-SIM: sim rig skeleton

**Unit:** Docker sim rig: `sim/docker-compose.yml` (toxiproxy 2.12.0, placeholder server,
real jellyfin, cabinet w/ real Xvfb+mpv+PipeWire), `sim/cabinet/Dockerfile`+entrypoint,
`sim/scripts/sim-test.sh`, root `Makefile` (sim-up/sim-down/sim-test). Cabinet is on
`cabnet` only, server/jellyfin on `srvnet` only, toxiproxy straddles both — the cabinet
physically cannot bypass chaos injection.

**Soak check:** N/A — soak stack is the next M-SIM unit.

**Evidence:**
- Docker daemon wasn't running; started manually (`dockerd`, DAEMON-OK-MANUAL).
- First `make sim-test` run: exit 0, `SIM-TEST PASS` (all 4 containers healthy, direct
  cabinet→server route correctly fails, partition breaks/restores link).
- Subagent verification: PASS, re-ran sim-test itself (exit 0), no fake-green found;
  3 tightening findings.
- Applied all 3: latency toxic now asserted ≥400ms (measured 689ms), mpv now renders a
  real frame to Xvfb (`--vo=x11 --frames=1 av://lavfi:testsrc`), grep-pipe race replaced
  with `toxiproxy-cli inspect`.
- Re-run after tightening: `SIM-TEST PASS`, latency 689ms (exit 0).

**Outcome:** GREEN.

---

## Iteration 3 — 2026-07-23 — M-SIM: detached soak stack

**Unit:** `sim/soak/`: standalone compose project (arcade-soak: toxiproxy, server,
cabinet, chaos driver w/ docker.sock), supervised 4Hz heartbeat (~20x accel, A-7),
chaos.sh (random 5-20s cadence: partitions 2-8s via toxiproxy toggle, kill -9 of
heartbeat), soak-status.py violation audit (V1 gap/no-respawn, V2 no-recovery after
partition, V3 FAIL outside chaos window, V4 stale). Makefile: soak-up/down/status.

**Soak check (rule):** stack didn't exist before this unit — N/A.

**Evidence:**
- Bugs found & fixed during bring-up: busybox date lacks %3N → chaos.log was in
  seconds (fixed: now_ms = %s*1000); busybox lacks %N → awk rand() seed was constant
  (fixed: counter+pid seed); FAILs logged just after READY were pre-proxy attempts
  stamped at curl completion (fixed: BOOT_GRACE_MS=3000).
- First audit attempt (90s, raced restart): correctly flagged V3s from pre-grace data
  — checker detects violations.
- Final audit after 120s of chaos: `make soak-status` exit 0 —
  "heartbeats: 467 (86.51% OK), partitions: 5, kills: 4, SOAK CLEAN".
  All FAILs inside chaos windows; supervisor respawned heartbeat after all 4 kill -9s.
- Subagent verification: launched (adversarial fake-violation injection included);
  verdict appended in a follow-up entry when it completes.

**Outcome:** GREEN (audit clean; verification verdict pending, will fix forward if flagged).
