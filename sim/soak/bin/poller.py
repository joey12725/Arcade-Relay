#!/usr/bin/env python3
"""Soak poller: samples arcaded's view of the cabinet every 0.5s.

One line per sample in /soak/status.log: <epoch_ms> ONLINE|OFFLINE|POLLERR
POLLERR means arcaded itself didn't answer (chaos never touches the server,
so POLLERR outside a chaos window is a violation too).
"""
import json
import time
import urllib.request

while True:
    ts = int(time.time() * 1000)
    try:
        with urllib.request.urlopen("http://server:8000/status", timeout=1) as r:
            st = json.load(r)
        line = f"{ts} {'ONLINE' if st['online'] else 'OFFLINE'}"
    except Exception:  # noqa: BLE001
        line = f"{ts} POLLERR"
    with open("/soak/status.log", "a") as f:
        f.write(line + "\n")
    time.sleep(0.5)
