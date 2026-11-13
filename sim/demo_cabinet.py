#!/usr/bin/env python3
"""A pretend cabinet for poking at arcaded without hardware or Docker.

Dials the server's /cab WebSocket exactly like cabd does, sends heartbeats
with a game "running", and answers the RPCs the dashboard asks for with
made-up but plausible data. Everything else gets a bare ok.

    python3 sim/demo_cabinet.py ws://127.0.0.1:8000/cab
"""
import asyncio
import sys
import time

import aiohttp

LIBRARY = {
    "nes": ["Star Patrol.nes", "Pixel Quest.nes", "Moon Miner.nes"],
    "snes": ["Neon Drift.sfc", "Castle Climb.sfc"],
    "genesis": ["Turbo Hedge Demo.md"],
    "gba": ["Tiny Tactics.gba"],
    "arcade": ["blastoff.zip", "starfury.zip"],
}
RUNNING = ("/roms/nes/Star Patrol.nes", "nes")
STARTED = time.time() - 754

ANSWERS = {
    "system.health": {"disk_total_h": "476G", "disk_free_h": "301G",
                      "disk_used_pct": 36.8, "uptime_h": "3d 4h 12m",
                      "load": ["0.42", "0.38", "0.35"], "temp_c": 48.5,
                      "clips": 24, "clips_h": "1.9G"},
    "library.list": {"systems": LIBRARY},
}


async def heartbeats(ws):
    while True:
        await ws.send_json({"kind": "event", "event": "heartbeat", "interval": 5,
                            "state": {"running": RUNNING[0], "system": RUNNING[1],
                                      "started_at": STARTED,
                                      "elapsed_s": time.time() - STARTED,
                                      "screensaver": False, "lifeline": False}})
        await asyncio.sleep(5)


async def main(url):
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, heartbeat=None) as ws:
            print(f"demo cabinet connected to {url}")
            beat = asyncio.create_task(heartbeats(ws))
            try:
                async for msg in ws:
                    req = msg.json()
                    if req.get("kind") != "req":
                        continue
                    method = req.get("method", "")
                    result = ANSWERS.get(method, {})
                    if method.startswith("game"):
                        result = {"running": RUNNING[0], "system": RUNNING[1]}
                    await ws.send_json({"id": req["id"], "kind": "res",
                                        "ok": True, "result": result})
            finally:
                beat.cancel()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000/cab"))
