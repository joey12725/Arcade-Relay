# DONE — Arcade Cabinet Platform (sim-scope implementation complete)

**Date:** 2026-07-24 · **Branch:** `claude/arcade-cabinet-impl-nn0cka`
**Spec:** `arcade-cabinet-spec.md` v0.1 · **Full history:** `state/JOURNAL.md` (37 iterations)

## Verdict

Every milestone (M-SIM, M0–M6) is certified: each ran its spec acceptance criteria
as `make accept-mN` and passed **3 consecutive times**, every work unit was
independently verified by an adversarial subagent that re-ran the tests itself, and
the platform accumulated **8h08m of violation-free chaos soak** (see Soak below).
Everything that only real hardware can prove is ledgered in `HARDWARE-TODO.md` —
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
On real hardware, run a continuous window (see HARDWARE-TODO).

## What exists

- `server/` — `arcaded` (WS hub, REST-only API, git state repo w/ commit-per-change,
  Jellyfin adapter w/ genuine 480p transcode, /media tunnel proxy, ROM queue,
  web UI at `/ui`), `arcadectl` (full CLI, --json everywhere, meaningful exit codes),
  `romdl.py` (fake backend behind the A-5 contract — swap in the real ROMDL here).
- `cabinet/` — `cabd` (reconnect-forever link, launcher+watchdog incl. hang
  detection, audio policy enforcement on real PipeWire, screensaver engine w/ mpv
  IPC + offline fallback, local launch socket + `cabd-launch`, renderer that stomps
  config drift before every launch, screenshots).
- `sim/` — the Docker rig (server + cabinet w/ real Xvfb/mpv/PipeWire + real
  Jellyfin, linked only through Toxiproxy) + soak stack + all test/acceptance
  scripts. `make sim-test / test-m0..m6 / accept-m0..m6 / soak-up / soak-status`.
- `skills/` + `AGENTS.md`/`CLAUDE.md` — proven cold-start agent onboarding (4/4).
- `state/` — the config-as-code source of truth.

## Deploying to real hardware

Work `HARDWARE-TODO.md` top to bottom (Debian install, real RetroArch/Pegasus +
redblue QML theme + UI-4 audit, evdev combos, Cloudflare tunnel, real ROMDL swap-in,
Sunshine/Moonlight + Greenlight sign-ins, 24h real-scale accept-m0, continuous 8h+
soak). Assumptions A-1..A-16 in `ASSUMPTIONS.md` record every sim-vs-real judgment
call, each with its hardware follow-up. Lifeline mode (§5.11) is the one spec
feature deliberately left for the hardware phase — it is inseparable from real
network interfaces and panel input.

## Numbers

37 journaled iterations · 8 milestones certified ×3 · 15 adversarial verifier
reviews (2 FAILs caught real bugs: a supervisor that died with cabd, and a
wireplumber race that silently disabled audio enforcement — both now permanent
regression tests) · ~5,400 clean launch cycles · 1,050+ partitions and dozens of
kill -9s survived · 0 product violations.
