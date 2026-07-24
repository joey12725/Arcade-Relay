# STATE

**Updated:** 2026-07-24 iteration 46 — deploy-readiness phase (P1–P7)

## Current position
- All original milestones (M-SIM, M0–M6) CERTIFIED 3/3; 8h clean soak done;
  DONE.md v1 written. User mandate: continue until spec fully done, fully
  tested, fully deploy-ready — no questions.
- Deploy-readiness phases:
  | Phase | Scope | Status |
  |-------|-------|--------|
  | P1 | evdev input layer + lifeline mode | **DONE** (verified) |
  | P2 | real RetroArch in sim, rendered-config proof | **DONE** (verified) |
  | P3 | Pegasus theme + state-generated collections + steam | **DONE** (findings applied 90baff2) |
  | P4 | remote view (b8d91c8, verifier PASS) + save-backup (1ca08b5+2e0f550, verifier PASS) + CLI completeness (dac6859, verifier running) | **DONE** pending P4.3 verifier |
  | P5 | provisioning (install.sh, systemd units, openbox autostart, udev) | next |
  | P6 | DEPLOY.md runbook | pending |
  | P7 | full regression (all test-* green, accept chains 3x, soak clean, verifier sweep) → DONE.md v2 → stop loop | pending |
- Consecutive failures on current unit: 0
- NOTE: loop self-paced; re-arm ScheduleWakeup with the P-phase /loop prompt
  verbatim each turn (60–300s active, 1800s blocked).
- NOTE: dockerd must be started manually after every host VM reclaim
  (`nohup dockerd &`); check `docker info` first. Soak restarts count
  cumulatively per A-16.

## Environment facts
- Dev box: Linux container with native Docker 29.x + Compose v5. See A-1.
- Repo: joey12725/Arcade-Relay, branch `claude/arcade-cabinet-impl-nn0cka` (push target).
- Spec: `arcade-cabinet-spec.md` at repo root (v0.1 2026-07-22).
- pip TLS blocked → apt python3-* packages; GitHub blocked by session proxy (A-18).

## Soak stack
- RUNNING (project arcade-soak). Check first each iteration: `make soak-status`
  must exit 0; any violation preempts the roadmap. Cumulative segments per A-16.

## Milestone status (original)
M-SIM, M0, M1, M2, M3, M4, M5, M6: all **DONE**, 3/3 accept passes each.
15+ adversarial verifier reviews; 2 historic FAILs caught real bugs
(supervisor death on cabd crash, wireplumber startup race).

## Next unit
P5 provisioning: cabinet/provision/ + server/provision/ — install.sh per
machine, systemd units (cabd, pegasus, steam, arcaded; Restart=always),
openbox autostart (xset s off -dpms), udev controller rules. Validate with
shellcheck + systemd-analyze verify in a container if feasible (document
which validation ran). Then P6 DEPLOY.md, then P7 full regression + DONE v2.
