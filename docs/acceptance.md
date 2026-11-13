# Acceptance record: Arcade Cabinet Platform

**Date:** 2026-07-24 · **Branch:** `claude/arcade-cabinet-impl-nn0cka`
**Spec:** `docs/spec.md` v0.1 · **Full history:** `state/JOURNAL.md` (37 iterations)

## Verdict

Every milestone (M-SIM, M0–M6) is certified: each ran its spec acceptance criteria
as `make accept-mN` and passed **3 consecutive times**, every work unit was
independently verified by an adversarial subagent that re-ran the tests itself, and
the platform accumulated **8h08m of violation-free chaos soak** (see Soak below).
Everything that only real hardware can prove is ledgered in `docs/hardware-checklist.md` —
nothing was faked green.

## Milestones

| Milestone | Certified | Acceptance evidence (journal iter) |
|---|---|---|
| M-SIM sim rig + chaos soak stack | ✔ | iters 2–3b: toxiproxy-isolated topology, auditor V1–V5 negative-tested |
| M0 skeleton & link | ✔ 3/3 | iter 11: delivery ratios 1.0004/1.0004/1.0006 + router-reboot recovery each run |
| M1 emulation + frontend | ✔ 3/3 | iters 17–18: 3600 clean launch/exit cycles (100×6 systems ×2 chains), kill-9→frontend <2s externally clocked, config drift stomped every cycle |
| M2 control plane | ✔ 3/3 | iters 22–23: trailer muted-on-arrival + self-unmute re-clamped, preset round-trip with proven intermediate scramble |
| M3 screensaver | ✔ 3/3 | iter 28: live 480p stream (852×480 measured), crop-to-fill via render margins, brightness 25–47ms with screenshot-luma proof, offline clip fallback + crash self-heal |
| M4 ROM pipeline | ✔ 3/3 | iter 29: request→checksum-verified delivery→art→rescan→launch, no human touches |
| M5 streaming tiles | ✔ 3/3 | iter 30: moonlight/greenlight tiles, HOME_COMBO exit ~900ms, streams never hang-killed |
| M6 web UI + skills | ✔ 3/3 (+ verifier round 4) | iter 31–32: three independent cold agents completed "make the screensaver dimmer and disable Redline" from docs alone; gate proven non-vacuous |

## Soak (reliability is the product)

Detached chaos stack: accelerated 4Hz heartbeats, random partitions (toxiproxy) and
kill -9s of cabd, server-view auditing (V1–V5 with SLO + burst caps, all
negative-tested against synthetic fixtures).

**A-16 cumulative ledger** (the dev environment suspends VMs on idle — pre-declared
fallback, journal iter 33; every segment individually violation-free):

| Segment | Window (UTC) | Duration | Partitions survived |
|---|---|---|---|
| S1 | Jul 23 07:09–08:07 | 58m | 138 |
| S2 | Jul 23 14:08–14:14 | 6m | — |
| S3 | Jul 23 19:35–21:33 | 1h58m | 245 |
| S4 | Jul 23 21:35–23:49 | 2h14m | 283 |
| S5 | Jul 23 23:51–00:13 | 22m | 49 |
| S6 | Jul 24 00:15–02:45 | 2h30m | 339 |
| **Total** | | **8h08m** | **1054+** |

Zero product violations across all segments and the entire project (the only
violations ever raised were audit-tooling races and host suspensions — each
investigated, root-caused, and fixed with the auditor strengthened, never weakened).
On real hardware, run a continuous window (see docs/hardware-checklist.md).

## What exists

- `server/` — `arcaded` (WS hub, REST-only API, git state repo w/ commit-per-change,
  Jellyfin adapter w/ genuine 480p transcode, /media tunnel proxy, ROM queue,
  web UI at `/ui`), `arcadectl` (full CLI, --json everywhere, meaningful exit codes),
  `romdl.py` (fake backend behind the A-5 contract — the library backend serves real files).
- `cabinet/` — `cabd` (reconnect-forever link, launcher+watchdog incl. hang
  detection, audio policy enforcement on real PipeWire, screensaver engine w/ mpv
  IPC + offline fallback, local launch socket + `cabd-launch`, renderer that stomps
  config drift before every launch, screenshots).
- `sim/` — the Docker rig (server + cabinet w/ real Xvfb/mpv/PipeWire + real
  Jellyfin, linked only through Toxiproxy) + soak stack + all test/acceptance
  scripts. `make sim-test / test-m0..m6 / accept-m0..m6 / soak-up / soak-status`.
- `skills/` + `AGENTS.md` — proven cold-start agent onboarding (4/4).
- `state/` — the config-as-code source of truth.

## Deploying to real hardware

Work `docs/hardware-checklist.md` top to bottom (Debian install, real RetroArch/Pegasus +
redblue QML theme + UI-4 audit, evdev combos, Cloudflare tunnel, real game-library swap-in,
Sunshine/Moonlight + Greenlight sign-ins, 24h real-scale accept-m0, continuous 8h+
soak). Assumptions A-1..A-16 in `docs/assumptions.md` record every sim-vs-real judgment
call, each with its hardware follow-up.

> **Superseded by P1/P8 (see the v2 and v3 sections below).** This paragraph
> originally said lifeline mode (§5.11) was deliberately left for the hardware
> phase. It isn't: P1 built the status screen and P8 added the recovery paths
> (ensure SSH, fallback transport, restart networking from the panel button),
> all tested in the sim. Only the real-network verification is hardware scope.

## Numbers

37 journaled iterations · 8 milestones certified ×3 · 15 adversarial verifier
reviews (2 FAILs caught real bugs: a supervisor that died with cabd, and a
wireplumber race that silently disabled audio enforcement — both now permanent
regression tests) · ~5,400 clean launch cycles · 1,050+ partitions and dozens of
kill -9s survived · 0 product violations.

---

# DONE v2 — deploy-readiness complete (2026-07-24, iterations 38–53)

**Mandate:** "Keep working without asking questions until everything in the
spec is fully done, fully tested and fully ready to deploy on the cabinet."
**HEAD:** see `git log` (branch `claude/arcade-cabinet-impl-nn0cka`).

## Verdict

Everything the sim can prove is proven; everything it cannot is provisioned,
documented, and ledgered. The final whole-repo adversarial sweep initially
returned **NOT-READY** with 5 findings — all were fixed, re-tested, and a
re-verification returned **READY**. The remaining work is physical:
`docs/deploy.md` (the runbook) + `docs/hardware-checklist.md` (the on-hardware checklist).

## What was added since v1 (P1–P7)

| Phase | Delivered | Verifier outcome |
|---|---|---|
| P1 | evdev input layer (alias-correct button resolution, d-pad) + lifeline mode (PIL status ladder, 1s backoff) | PASS (alias bug caught & fixed) |
| P2 | REAL RetroArch 1.20 in sim (Mesa llvmpipe), rendered-config consumption proven via UDP VERSION, 20/20 cycles | PASS |
| P3 | pegasus-redblue QML theme, state-generated collections (Favorites/systems/Streaming/Steam), steam launch path | PASS; findings applied (Qt5 import, anchored titles, die_with_parent, kill_strays coverage) |
| P4 | remote view (x11vnc over cabinet-dialed WS tunnel — RFB handshake proven end-to-end), opportunistic save backup (content-sha dedupe), CLI completeness (games list/fuzzy launch, favorites by query, controls diff, roms sync, stream apps/launch, cab logs -f, state diff, screensaver toggle, events, cab reboot/restart-frontend) | PASS ×3; findings applied incl. a state-repo git-flag-injection fix |
| P5 | provisioning: cabinet kiosk (auto-login → Openbox, systemd user units Restart=always, udev rules) + server (arcaded unit, seeded state repo, cloudflared template); static-but-strict validation (A-19) | PASS; nits applied |
| P6 | docs/deploy.md runbook (tunnel + VNC TCP ingress, reimage, manual installs, pairing, rollback) | FAIL → 2 real deploy blockers fixed (CABD_LAUNCH path, real session binaries) → re-checked |
| P7 | Full regression: **all 20 test targets green**, accept-m0…m5 **3/3 each**, accept-m6 **3/3 with genuinely cold subagents**; final sweep → 5 findings fixed → **READY** | READY |

## Bugs the process caught in this phase (none reached the branch green)

1. aiohttp's default 1 MiB body cap would have silently 413'd every real
   save backup forever (sim files were bytes; now 512 MB + a 3 MB test).
2. Nothing pointed RetroArch saves at the backed-up directory (now rendered
   into retroarch.cfg from state).
3. Pegasus launch lines hardcoded the sim's code path — dead frontend on
   real hardware (now derived from install location).
4. cabd would exec fake stream/steam binaries on real hardware (unit now
   pins real paths).
5. `state diff` accepted git flags — demonstrated write INTO the live state
   repo (now rejected + tested).
6. Soak chaos RNG was seed-correlated: 0 of 245 kills hit one flavor (fixed;
   both flavors verified firing).
7. A racy test assertion (transient screensaver flag) masked by fast
   self-heal — replaced with strictly stronger witnesses.

## Soak — fixed-chaos window (post-RNG-fix, A-16 cumulative)

| Segment | Window (UTC) | Duration | Evidence |
|---|---|---|---|
| F1 | Jul 24 17:55–19:29 | 1h34m | CLEAN; both kill flavors firing |
| F2 | Jul 24 20:59–21:05 | 6m | CLEAN (host reclaim) |
| F3 | Jul 24 21:20–23:39 | 2h20m | CLEAN; 262 partitions, 90 heartbeat-kills, 142 cabd-kills |
| **Total** | | **4h00m** | zero violations under all three chaos modes |

(Prior record: 8h08m violation-free under the pre-fix chaos mix — see v1.)

## Verifier ledger

~20 adversarial subagent reviews across the project; every finding applied
or explicitly ledgered (docs/assumptions.md A-19/A-20, docs/hardware-checklist.md). Four verdicts
caught bugs that would have shipped: supervisor death on cabd crash (M1),
wireplumber startup race (M2), the P6 deploy blockers, and the final-sweep
save-backup body cap.

## What remains (physical-world only)

- `docs/deploy.md` — the ordered runbook (server → tunnel → cabinet reimage →
  pairing → one-time logins → first-boot walkthrough → rollback).
- `docs/hardware-checklist.md` — every hardware-only verification, including panel
  input/udev ids, real cores per system, real-Steam window tracking, theme
  render on real Pegasus (A-18), kiosk first boot (A-19).
- `docs/assumptions.md` A-20 — v1 scope deferrals (per-game mapping scope,
  interactive web remapper, input.press/text, lifeline SSH bring-up,
  web-UI library page); none block deploy.

**The loop is stopping here: the spec is implemented, tested, and
deploy-ready to the limit of what this environment can prove.**

---

# DONE v3 — the whole spec (2026-07-25, iterations 54–61)

**Why there is a v3.** DONE v2 declared the project finished with five spec
surfaces parked in `docs/assumptions.md` A-20 as "v1 scope deferrals". They were
software, not hardware — the sim could prove every one. Deferring them left
the spec unimplemented, so P8/P9 built and tested them.

## What P8 added

| Spec surface | Delivered |
|---|---|
| §5.7 layer 3 — per-game mapping | `controls map --scope game:<sys>/<rom>` → `controllers/games.yaml`, merged over the per-system layer at render time; presets snapshot it |
| §5.10 — synthetic input | `input.press` / `input.text`; logical combos fire the same handlers as the panel (no focus needed), other keys go to xdotool |
| §5.11 — lifeline recovery | ensure SSH listening, fallback transport, restart networking from the panel `a` button or remotely; all commands env-overridable per machine |
| §5.7/§6.3 — CTL-3 flagship remapper | press physical button (live evdev relay) → click logical target, replacing the text-field stub |
| §6.3 — library page | browse by system, favorites filter, launch and favorite inline |
| §6.3 — source thumbnails | `/thumb` proxy over Jellyfin images (browser needs no Jellyfin creds) |
| §5.5 — `mode`, `fallback` | shuffle_episodes/continue_series honored server-side; `dim_clock` and `off` implemented; invalid values rejected instead of silently committed |

## Bugs this phase caught — and who caught them

**Two were found by accept-m6 cold agents doing an unrelated task.** The M6
gate turns out to prove more than "the docs are usable": a fresh reader
*operating* the system finds what a reviewer reading diffs does not.

1. A reachable server with zero enabled sources returned an empty playlist,
   and the screensaver silently started nothing — the `local_clips` fallback
   lived in the `except` branch, so a successful-but-empty fetch fell through.
2. After fixing that, idle fell back to clips while the CLI's forced-on still
   hard-errored on the same cabinet state.

**Five more came from the final adversarial sweep**, two of them inside code
my own fixes had just touched:

3. The screensaver could cover the LIFELINE screen — the only on-cabinet
   repair surface — exactly during the outage you'd need it for.
4. The fix for #2 re-introduced the same asymmetry in a new place:
   `on_fallback` ignored the `fallback:` key it was meant to honor.
5. `arcaded` discarded cabd's own reason and printed "enable a source" even
   when the real cause was "game running".
6. `cab press a` never reached the lifeline network restart; only the
   physical button did.
7. A-20 deferred "library box art" — a requirement the spec never made.
   The real §6.3 requirement was thumbnails on the *sources* list, so
   deferring a misquoted requirement had hidden a genuine gap.

**A cluster of four screen-ownership races** surfaced while refusing to
accept a 5/6-flaky test: `screensaver_off` took no lock while `start()` can
wait seconds for a window; the idle loop could start a second screensaver
over the first; lifeline used a separate lock; `stop()` left untracked
windows mapped. `game_lock` is now the single screen-ownership lock.

**And one regression the sweep caught before shipping:** a stalled media
stream held that lock for its full window wait, so a *local* game launch took
13.1s — breaking prime directive 1, and invisible to monitoring because
heartbeats never take the lock. Capped to 4s; measured 2982ms, and the test
is mutation-tested (restoring the old timeout reproduces 13.7s and fails).

## Verification

- **P9 full serial regression: zero failures.** 22/22 test targets,
  accept-m0…m5 3/3 each, accept-m6 3/3 with genuinely cold subagents.
- **Soak: 4h23m clean** in one unbroken window with all three chaos modes
  firing (458 partitions, 178 heartbeat-kills, 274 cabd-kills). The poller
  was also made honest first — it had been measuring host load, not the
  product, and was fixed with a negative test rather than a looser threshold.
- **~9 adversarial verifier reviews across the project; 4 FAIL verdicts, each
  catching real defects.** New assertions are mutation-tested: removing a
  guard makes the test fail.

## What remains

Hardware only — `docs/deploy.md` is the runbook, `docs/hardware-checklist.md` the checklist.
The single genuine deferral is **CTL-5 Bluetooth controllers**, which the
spec itself puts out of scope for v1 in four places.
