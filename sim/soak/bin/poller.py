#!/usr/bin/env python3
"""Soak poller: samples arcaded's view of the cabinet every 0.5s.

One line per sample in /soak/status.log: <epoch_ms> ONLINE|OFFLINE|POLLERR
POLLERR means arcaded genuinely didn't answer (chaos never touches the
server, so POLLERR outside a chaos window is a violation too).

A single slow response is NOT a POLLERR. The soak shares a host with the
test suite and CI; a 1s timeout on a loaded box measures the host, not the
product. Each sample therefore retries briefly before declaring failure, and
the reason is recorded so a genuine outage (connection refused — the server
process is gone) stays distinguishable from load-induced slowness.
"""
import json
import time
import urllib.error
import urllib.request

TIMEOUT_S = 3.0        # generous: we are testing arcaded, not the host's CPU
ATTEMPTS = 3           # a real outage fails all three; a slow moment does not
RETRY_GAP_S = 0.15


def sample():
    last = None
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen("http://server:8000/status",
                                        timeout=TIMEOUT_S) as r:
                st = json.load(r)
            return "ONLINE" if st["online"] else "OFFLINE", None
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt + 1 < ATTEMPTS:
                time.sleep(RETRY_GAP_S)
    return "POLLERR", last


while True:
    ts = int(time.time() * 1000)
    state, err = sample()
    line = f"{ts} {state}"
    if err is not None:
        line += f" {type(err).__name__}"   # audit trail: refused vs timeout
    with open("/soak/status.log", "a") as f:
        f.write(line + "\n")
    time.sleep(0.5)
