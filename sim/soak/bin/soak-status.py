#!/usr/bin/env python3
"""Audit the soak logs for reliability violations.

Violation rules (v1, placeholder-heartbeat era — tighten when cabd lands):
  V1  attempt gap: consecutive heartbeat log lines more than GAP_MS apart
      outside a grace window after a KILL9 (supervisor must respawn fast).
  V2  no recovery: a FAIL streak that extends more than RECOVER_MS past the
      matching PARTITION-END (link restored but heartbeats stayed dead).
  V3  spontaneous failure: any FAIL not inside a partition window
      (+/- slack) and not within RECOVER_MS after one.
  V4  liveness: newest heartbeat line older than STALE_MS.
  V5  server view: poller sample OFFLINE/POLLERR outside any partition
      window and not within CABD_GRACE_MS after a cabd kill -9 (cabd must
      reconnect fast enough that arcaded never reports the cabinet down).

Exit 0 = clean, exit 1 = violations found, exit 2 = can't read logs.
"""
import os, sys, time, pathlib

DATA = pathlib.Path(os.environ.get(
    "SOAK_DATA", pathlib.Path(__file__).resolve().parent.parent / ".data"))
GAP_MS = 5000        # supervisor must restart heartbeat within 5s of a kill
RECOVER_MS = 10000   # heartbeats must recover within 10s of partition end
SLACK_MS = 1500      # in-flight requests around partition edges
STALE_MS = 15000
CABD_GRACE_MS = 8000  # cabd kill -> respawn (0.2s) + reconnect (backoff cap 2s)
                      # + offline threshold (3*0.25s) + margin

def read(name):
    p = DATA / name
    if not p.exists():
        print(f"soak-status: missing {p}", file=sys.stderr)
        sys.exit(2)
    out = []
    for line in p.read_text().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1]))
    return out

hb = read("heartbeat.log")
chaos = read("chaos.log")

# Soak officially starts at the chaos driver's READY line (proxy created);
# heartbeat FAILs before that are bootstrap noise, not violations. A short
# grace after READY covers attempts started pre-READY but logged after
# completion (lines are stamped when curl finishes, up to --max-time later).
BOOT_GRACE_MS = 3000
ready = next((ts for ts, m in chaos if m.startswith("READY")), None)
if ready is None:
    print("soak-status: no READY line in chaos.log yet", file=sys.stderr)
    sys.exit(2)
hb = [(ts, m) for ts, m in hb if ts >= ready + BOOT_GRACE_MS]

if not hb:
    print("soak-status: heartbeat.log empty", file=sys.stderr)
    sys.exit(2)

partitions = []   # (start_ms, end_ms)
kills = []        # heartbeat.sh kills (V1/V3 exemptions)
cabd_kills = []   # cabd kills (V5 exemptions)
start = None
for ts, msg in chaos:
    if msg.startswith("PARTITION-START"):
        start = ts
    elif msg.startswith("PARTITION-END") and start is not None:
        partitions.append((start, ts))
        start = None
    elif msg.startswith("KILL9 cabd"):
        cabd_kills.append(ts)
    elif msg.startswith("KILL9"):
        kills.append(ts)
if start is not None:
    partitions.append((start, time.time() * 1000))  # still partitioned

def in_partition(ts):
    return any(s - SLACK_MS <= ts <= e + RECOVER_MS + SLACK_MS for s, e in partitions)

def near_kill(ts):
    return any(k <= ts <= k + GAP_MS + SLACK_MS for k in kills)

violations = []
prev_ts = hb[0][0]
for ts, msg in hb[1:]:
    # A gap is excused only if a kill actually falls inside it AND the
    # heartbeat resumed within the respawn SLA measured from that kill.
    if ts - prev_ts > GAP_MS and not any(
            prev_ts - SLACK_MS <= k <= ts and ts <= k + GAP_MS + SLACK_MS
            for k in kills):
        violations.append(f"V1 gap {ts - prev_ts}ms ending at {ts}")
    prev_ts = ts

for ts, msg in hb:
    if msg == "FAIL" and not in_partition(ts) and not near_kill(ts):
        violations.append(f"V3 FAIL outside chaos window at {ts}")

for s, e in partitions:
    tail = [(ts, m) for ts, m in hb if e < ts <= e + RECOVER_MS]
    if tail and not any(m == "OK" for _, m in tail):
        violations.append(f"V2 no recovery within {RECOVER_MS}ms after partition ended {e}")

age = time.time() * 1000 - hb[-1][0]
if age > STALE_MS:
    violations.append(f"V4 heartbeat stale: last line {int(age)}ms old")

# V5: arcaded's own view of the cabinet, sampled by poller.py
status = read("status.log")
status = [(ts, m) for ts, m in status if ts >= ready + BOOT_GRACE_MS]
if not status:
    print("soak-status: status.log empty/pre-ready — poller not running?",
          file=sys.stderr)
    sys.exit(2)

def near_cabd_kill(ts):
    return any(k - SLACK_MS <= ts <= k + CABD_GRACE_MS for k in cabd_kills)

for ts, m in status:
    if m != "ONLINE" and not in_partition(ts) and not near_cabd_kill(ts):
        violations.append(f"V5 server saw {m} outside chaos window at {ts}")

sage = time.time() * 1000 - status[-1][0]
if sage > STALE_MS:
    violations.append(f"V4 status.log stale: last line {int(sage)}ms old")

ok = sum(1 for _, m in hb if m == "OK")
fail = sum(1 for _, m in hb if m == "FAIL")
total = ok + fail
pct = 100.0 * ok / total if total else 0.0
print(f"heartbeats: {total} ({pct:.2f}% OK), partitions: {len(partitions)}, kills: {len(kills)}")

if violations:
    print(f"VIOLATIONS ({len(violations)}):")
    for v in violations[:20]:
        print(f"  {v}")
    sys.exit(1)
print("SOAK CLEAN")
