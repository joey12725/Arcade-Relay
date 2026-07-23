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
