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

---

## Iteration 3b — 2026-07-23 — Verifier verdict + V1 fix

**Verifier verdict on soak unit:** FAIL — V1 (gap w/o respawn) was dead code: the kill
exemption `k <= prev_ts+GAP and ts >= k` is trivially true for any earlier kill.
V2/V3/V4 confirmed genuine by executed injections (each exit 1). Timestamps, grace
windows, and no-test-weakening all confirmed.

**Fix:** V1 exemption now requires the kill to fall inside the gap AND resume within
kill+GAP+SLACK. Also: SOAK_DATA env override so negative tests are scriptable.

**Evidence:**
- Injected 8s heartbeat hole ≥15s from every kill (script in journal-referenced
  commit): `SOAK_DATA=<copy> soak-status.py` → exit 1, "V1 gap 8093ms" (+V2, hole
  crossed a partition end). Detection works.
- Live audit after fix: `make soak-status` exit 0 — 1319 heartbeats (84.31% OK),
  15 partitions, 9 kills, SOAK CLEAN — real kill-respawn gaps correctly excused.

**Outcome:** GREEN (verifier's confirmed bug fixed, negative-tested, live clean).

---

## Iteration 4 — 2026-07-23 — M0: arcaded + cabd skeleton over the chaos link

**Soak check:** `make soak-status` exit 0 — 2022 heartbeats (84.17% OK), 24 partitions,
13 kills, SOAK CLEAN.

**Unit:** Real daemons replace the placeholder: `server/arcaded.py` (aiohttp: WS hub
/cab, REST /status /events /cab/exec, RPC w/ pending-future map, newest-connection-wins),
`cabinet/cabd.py` (outbound WS via toxiproxy, heartbeats @HEARTBEAT_SECONDS, exp backoff
0.5→cap jittered ±20% forever, system.exec RPC), `server/arcadectl` (stdlib CLI: status,
cab exec, --json, meaningful exit codes). Sim: cabd supervised in cabinet entrypoint
(Restart=always analog), hb 0.25s (~20x), backoff cap 2s. New `make test-m0`.

**Evidence:**
- pip TLS blocked by agent proxy in docker build (SSL: CERTIFICATE_VERIFY_FAILED) →
  switched server image to debian + python3-aiohttp via apt (same as cabinet). Journaled
  as env constraint, not code fault.
- `make test-m0` → TEST-M0 PASS (exit 0): online via heartbeats; exec round-trip
  "m0-roundtrip"; remote `false` exits non-zero; partition → offline; unaided reconnect;
  exec again post-reconnect.
- `make sim-test` → SIM-TEST PASS (exit 0), latency toxic 688ms — no regression with
  real arcaded behind the proxy.
- Subagent verification launched (re-runs both tests + reviews for weakening); verdict
  appended when it completes.

**Outcome:** GREEN pending verifier (both suites pass locally).

---

## Iteration 4b — 2026-07-23 — M0 verifier verdict + hardening

**Verifier verdict on M0 skeleton:** PASS (test-m0 exit 0, sim-test exit 0 re-run by
verifier; heartbeat-vs-socket-closed detection confirmed out-of-band with an upstream
timeout toxic). 4 gaps flagged, all applied:
1. test-m0 step 4b: hang case (timeout=0 upstream toxic — socket open, heartbeats
   blocked) → offline by heartbeat age alone; recover on toxic removal.
2. cabd: backoff resets on successful connect (was: only on clean session end).
3. arcaded: pending RPC futures failed immediately on disconnect (503 now, not 15s 504).
4. arcadectl: exit 4 for API/transport errors vs 1 for remote rc!=0.

**Evidence:** `make test-m0` → TEST-M0 PASS (exit 0) including new step 4b.

**Outcome:** GREEN.

---

## Iteration 5 — 2026-07-23 — M0 acceptance harness

**Soak check:** exit 0 — 4856 heartbeats (84.41% OK), 56 partitions, 32 kills, SOAK CLEAN.

**Unit:** `make accept-m0` (sim/scripts/accept-m0.sh) per A-9 (recorded): criterion 1 =
>=99% server-side delivered beats over ACCEPT_WINDOW_S (default 1200s) at 0.25s;
criterion 2 = router reboot (toxiproxy restart wipes in-memory proxy = dead link,
recreate = router back) -> offline detected, unaided reconnect <=60s with NEW WS
connection (connects counter must increase) + exec round-trip. arcaded: added
delivered-beats counter to /status.

**Evidence:**
- `ACCEPT_WINDOW_S=20 make accept-m0` smoke → ACCEPT-M0 PASS, delivered=81/80
  ratio=1.0125, reboot recovery + exec OK (exit 0).
- Full run 1/3 (1200s window) launched in background; result in next entry.

**Outcome:** GREEN (harness); acceptance passes 0/3 so far.

---

## Iteration 6 — 2026-07-23 — Soak migrated to real daemons + V5 rule

**Context:** accept-m0 run 1/3 (1200s) executing in background; accept-m0 harness
verifier returned PASS (design honest; minor findings: HB duplication, ratio >1
cosmetic — deferred until run 1 finishes since editing a script bash is executing
corrupts it).

**Unit:** Soak stack now runs the real product: soak-server = arcaded (build
server/), soak-cabinet runs real cabd (supervised, SERVER_WS_URL via toxiproxy,
hb 0.25s, backoff cap 2s) alongside the HTTP prober; chaos now kill -9s cabd too
(50/50 vs heartbeat.sh); new soak-poller samples arcaded /status every 0.5s to
status.log; soak-status.py gains V5 (server saw OFFLINE/POLLERR outside partition
windows and beyond 8s cabd-kill grace) + status.log staleness under V4.

**Evidence:**
- `make soak-up` → all 6 containers healthy (exit 0).
- 120s chaos audit running in background (bc55j798b); result in next entry.

**Outcome:** stack green at bring-up; audit pending.

**Iter 6 audit result:** `make soak-status` exit 0 after 120s chaos on real daemons —
467 HTTP probes (88.44% OK), 4 partitions, 3 cabd kill -9s (chaos randomly picked cabd
every time), cabd logged 8 connects (respawn+reconnect after every kill/partition),
status.log all ONLINE outside chaos windows: V5 active and clean. SOAK CLEAN.

---

## Iteration 7 — 2026-07-23 — M1 unit 1: fake game binaries (A-8)

**Soak check:** covered this turn by iter-6 audit (SOAK CLEAN on real daemons).

**Unit:** `sim/fakes/retroarch` (real mpv X window titled retroarch-fake, RetroArch-style
UDP command iface on 55355: QUIT/PAUSE_TOGGLE/GET_STATUS, SIGTERM-clean, --crash-after N
exits 70, --hang-after N stops answering but keeps window — for EMU-3 watchdog tests) and
`sim/fakes/pegasus` (persistent frontend window, SIGTERM-clean). `/fakes` volume added to
sim cabinet; `make test-fakes` scripted (runs post-accept-run since the new mount
recreates the container).

**Evidence (live validation in running cabinet via docker cp — zero link impact):**
- pegasus window OK; retroarch window OK (xdotool search by title)
- UDP: "GET_STATUS PLAYING /roms/snes/smw.sfc"; QUIT OK: window gone
- SIGTERM OK: pegasus gone; crash exit: 70

**Outcome:** GREEN (manual validation; scripted test + subagent verify after accept run 1).
Committed as c782ada (journal entry landed one commit later due to a cwd slip).

---

## Iteration 8 — 2026-07-23 — accept-m0 run 1 + heartbeat drift fix

**Evidence:**
- accept-m0 run 1/3 (pre-fix code): exit 0 — ACCEPT-M0 PASS, delivered=4780/4800
  ratio=0.99583, router reboot recovered, exec OK.
- Ratio margin only 0.58pp above floor; root cause = cabd send-then-sleep heartbeat
  period drift (~0.251s), predicted by the harness verifier. Fixed: absolute-time
  scheduling in heartbeat_loop (resync-don't-burst on stall). Acceptance series
  RESTARTED so all 3 passes certify final code (run 1 pre-fix pass discarded).
- `make test-fakes` → TEST-FAKES PASS (scripted, /fakes mount, cabinet recreated).
- `make test-m0` → TEST-M0 PASS with drift-fixed cabd.
- 3-run acceptance chain launched in background.

**Outcome:** GREEN; acceptance passes 0/3 (series restarted on final code).

---

## Iteration 9 — 2026-07-23 — M1 unit 2: launcher + watchdog module

**Context:** accept-m0 3-run chain executing in background (bm3qlf3ge); this unit is a
NEW standalone file (cabinet/launcher.py) — nothing the running stack loads was touched.

**Unit:** GameSession (spawn fake retroarch, wait window <=5s, focus), kill escalation
(UDP QUIT -> SIGTERM -> SIGKILL, then refocus frontend), watchdog poll 0.25s with 2s
recovery budget, selftest matrix (launch/clean-kill/kill -9/self-crash) run in-container.

**Evidence:** `docker compose exec cabinet python3 /cabinet/launcher.py selftest` →
LAUNCHER-SELFTEST PASS: kill->frontend 0.06s, death->frontend 0.01s (post-detection;
poll adds <=0.25s), crash->frontend 0.02s. Exit 0. Committed as 8aec21d.
Verifier subagent running (exec-only constraint; independently times kill-9->focus).

**Outcome:** GREEN (selftest); external kill->focus timing lands in test-m1/accept-m1.

---

## Iteration 10 — 2026-07-23 — Launcher verifier verdict + hang detection

**Verifier verdict on launcher unit:** PASS (re-ran selftest exit 0; independent external
measurement death->focus 0.230s; hung QUIT-deaf game killed in 0.54s by escalation; no
path returns from kill() with process alive). 3 findings, all fixed:
1. Orphaned mpv windows on wrapper SIGKILL → PR_SET_PDEATHSIG in both fakes
   (verified: "PDEATHSIG OK: window died with wrapper").
2. Hangs invisible to watchdog → watch() now probes UDP GET_STATUS each poll;
   2s of silence with a live window = hung → kill escalation + refocus. New selftest
   scenario with --hang-after: "hang detected+killed+refocused in 3.28s total".
3. Timing honesty: kill-9 scenario now measures WALL time from SIGKILL (0.50s incl.
   detection); labels say detection->frontend where applicable; fe.wait() added.

**Evidence:** in-container selftest → LAUNCHER-SELFTEST PASS (exit 0), all 5 scenarios.

**Outcome:** GREEN. Accept-m0 3-run chain still executing (bm3qlf3ge).

---

## Iteration 11 — 2026-07-23 — M0 CERTIFIED (3/3 acceptance passes)

**Soak check:** exit 0 — 17841 probes, 199 partitions, SOAK CLEAN.

**Evidence (bm3qlf3ge, `for i in 1 2 3; do make accept-m0; done` exit 0):**
- Run 1: ACCEPT-M0 PASS delivered=4802/4800 ratio=1.00042
- Run 2: ACCEPT-M0 PASS delivered=4802/4800 ratio=1.00042
- Run 3: ACCEPT-M0 PASS delivered=4803/4800 ratio=1.00063
- Every run includes: offline detection on router reboot, unaided reconnect on a NEW
  WS connection, exec round-trip. Drift-fixed cabd delivers at theoretical max
  (ratios >=1.0 vs pre-fix 0.9958).

**M0 status: DONE** (accept-m0 passed 3 consecutive times per loop rules; real-scale
24h/tunnel/router run remains in HARDWARE-TODO.md per A-9).

---

## Iteration 11b — 2026-07-23 — M1: cabd/arcaded/arcadectl game lifecycle integration

**Unit:** cabd imports launcher: game.launch/game.kill/game.running RPCs (serialized via
lock, launch-over-running kills first), async watchdog task (death + UDP-silence hang →
kill + refocus + state clear), frontend supervision (spawn FRONTEND_BIN, respawn on
death). arcaded: POST /games/launch|kill (+args passthrough). arcadectl: games
launch/kill/running. Heartbeat state now carries running game. New `make test-m1` (6
steps, external kill-9 timing).

**Bugs found & fixed during bring-up:**
- Stale /tmp/.X0-lock after `docker compose restart` → Xvfb dead, frontend respawn loop.
  Entrypoint now clears X locks. (Real-hardware note: N/A — systemd manages X there.)
- test-m1 pgrep self-match on "hang.sfc" ([h]ang trick).
- mpv zombies: PID1 sleep can't reap → `init: true` on sim+soak cabinet services.

**Evidence:**
- `make test-m1` → TEST-M1 PASS (exit 0): launch visible in status; kill → frontend
  focused; kill -9 → frontend focused in 668ms (external clock, budget 2000ms); hung
  game auto-killed+recovered; frontend respawned <4s.
- Regressions: TEST-M0 PASS, SIM-TEST PASS, SOAK CLEAN.

**Outcome:** GREEN pending verifier.

---

## Iteration 12 — 2026-07-23 — Verifier FAIL on M1 lifecycle → supervisor + race fixes

**Verifier verdict:** FAIL — tests were green but the adversarial probe (kill -9 cabd
mid-game) exposed: entrypoint supervisor subshell inherits `set -e`, so any non-zero
cabd exit KILLED the supervisor → cabinet permanently offline, orphaned game+frontend,
stale running state forever. Exactly the failure mode this project exists to prevent.

**Fixes (all verifier findings):**
1. Supervisor: `|| true` on the cabd invocation (load-bearing comment added).
2. cabd startup: `launcher.kill_strays()` — post-crash reality convergence.
3. Frontend spawned with PR_SET_PDEATHSIG(SIGTERM) → dies with cabd, no double windows.
4. Watchdog race: recovery goes through `_recover_if_current()` under game_lock with
   identity check — can no longer clear/focus over a newly-launched session.
5. GameSession.kill() waits <=1s for window teardown → next launch can't bind the
   dying window.
6. test-m1 step 7 (permanent): launch → kill -9 cabd → online again <=15s, stale state
   cleared, orphan killed, exactly 1 frontend window.

**Evidence:** TEST-M1 PASS 7/7 (kill-9→frontend 514ms external); TEST-M0 PASS;
SIM-TEST PASS; SOAK CLEAN. (Soak supervisor already safe: cabinet-soak.sh is set -u.)

**Outcome:** GREEN.

---

## Iteration 13 — 2026-07-23 — M1: config rendering from state repo (CTL-4)

**Soak check:** SOAK CLEAN (exit 0).

**Unit:** State pipeline end-to-end: state/ YAMLs (cabinet.yaml, controllers/global.yaml,
controllers/systems/{snes,nes}.yaml) → arcaded builds bundle (pyyaml, STATE_DIR mount)
and pushes state.sync on every cabinet connect (+ POST /state/push, GET /state/bundle,
arcadectl state push|bundle) → cabd caches bundle atomically at /var/cache/
arcade-state.json (loads at startup: offline-first) → renderer.py renders
retroarch.cfg + <system>.rmp atomically before EVERY launch. New `make test-render`.

**Evidence:**
- TEST-RENDER PASS (exit 0): cache lands on connect; snes render shows a->b remap +
  stomp header; scrambled files stomped clean on relaunch; offline render (link cut,
  cabd kill -9 + respawn) works purely from cache; link restored, back online.
- Regressions: TEST-M1 PASS (7/7), TEST-M0 PASS.

**Outcome:** GREEN pending verifier.

---

## Iteration 14 — 2026-07-23 — Local launch surface (prime directive 1)

**Verifier verdict on render unit:** PASS (heal-from-poisoned-cache probe passed;
stomp ordering proven honest). Low-severity finding fixed: state.sync now rejects
non-dict bundles and load_state_cache ignores poisoned caches.

**Unit:** cabd Unix control socket (/tmp/cabd.sock; game.launch/kill/running, one JSON
line each way) + `cabd-launch` shim (what Pegasus will exec; exit 0/1/3). Launches
never touch the network. New `make test-local`.

**Evidence:**
- TEST-LOCAL PASS (exit 0): with the link CUT — local launch ok (window up, configs
  rendered from cache), local kill refocuses frontend; game launched offline becomes
  visible to the server after reconnect (heartbeat state converges); remote kill of a
  locally-launched game works.
- Regressions: TEST-M1 PASS, TEST-RENDER PASS, SOAK CLEAN.

**Outcome:** GREEN.

---

## Iteration 15 — 2026-07-23 — Favorites + git state repo + accept-m1 harness

**Verifier verdict on local-launch unit:** PASS (offline ordering genuine; shared code
paths; robust vs malformed/oversized/hanging socket input; failure paths clean; exit
codes verified live). Minor notes deferred (empty-reply json traceback; GameSession
pdeathsig — mitigated by kill_strays).

**Units (chained this turn):**
1. Server state repo is now a real git repo (entrypoint seeds /statework from RO mount;
   git commit per mutation — SRV-2). arcadectl state log.
2. Favorites: /favorites add/remove/list + state/library/favorites.yaml + push-on-change
   (offline cabinets converge via push-on-connect). arcadectl favorites family.
3. accept-m1 harness: per system 100 local-path cycles (launch → configs clean w/
   distinct per-system remap values to catch leakage → window → play → kill → frontend
   focused), every 10th cycle pre-scrambled; then 5x kill -9 recovery <2s external clock.
4. Remap yamls for genesis/arcade/gba/psx (distinct values).

**Evidence:**
- TEST-FAVORITES PASS (exit 0): add→list→git commit→cabinet cache; remove→all gone.
- ACCEPT_M1_CYCLES=3 smoke: ACCEPT-M1 PASS — 18 cycles 6 systems clean, kill-9 reps
  356-460ms.
- Full 3-run chain (100 cycles/system) launching in background.

**Outcome:** GREEN; M1 acceptance 0/3 (chain starting).
