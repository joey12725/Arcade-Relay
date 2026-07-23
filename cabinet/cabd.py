#!/usr/bin/env python3
"""cabd — cabinet daemon (M0 skeleton).

Maintains the outbound WebSocket to arcaded (spec §5.2 #1): reconnect with
exponential backoff (jittered, capped), forever. Sends heartbeats; executes
RPCs (system.exec first — spec: agents need escape hatches, security out of
scope by decree).

Env:
  SERVER_WS_URL       ws://host:port/cab
  HEARTBEAT_SECONDS   default 5 (sim runs accelerated, e.g. 0.25)
  CABD_BACKOFF_CAP    default 10 (seconds; sim uses 2 for fast tests)
"""
import asyncio
import json
import os
import random
import subprocess
import sys

import aiohttp

WS_URL = os.environ.get("SERVER_WS_URL", "ws://toxiproxy:8666/cab")
HB = float(os.environ.get("HEARTBEAT_SECONDS", "5"))
BACKOFF_CAP = float(os.environ.get("CABD_BACKOFF_CAP", "10"))
BACKOFF_START = 0.5


def log(*a):
    print("[cabd]", *a, file=sys.stderr, flush=True)


async def handle_req(ws, data):
    method = data.get("method")
    params = data.get("params") or {}
    try:
        if method == "system.exec":
            p = subprocess.run(["/bin/sh", "-c", params["cmd"]],
                               capture_output=True, text=True, timeout=30)
            result = {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
        else:
            await ws.send_json({"id": data.get("id"), "kind": "res", "ok": False,
                                "error": f"unknown method {method}"})
            return
        await ws.send_json({"id": data.get("id"), "kind": "res", "ok": True,
                            "result": result})
    except Exception as e:  # noqa: BLE001 — report, never die on a bad RPC
        await ws.send_json({"id": data.get("id"), "kind": "res", "ok": False,
                            "error": repr(e)})


async def heartbeat_loop(ws):
    while True:
        await ws.send_json({"kind": "event", "event": "heartbeat", "interval": HB,
                            "state": {"running": None, "screensaver": False}})
        await asyncio.sleep(HB)


async def session(sess, on_connect):
    async with sess.ws_connect(WS_URL, heartbeat=None) as ws:
        log("connected to", WS_URL)
        on_connect()  # reset backoff on successful CONNECT, not clean exit
        hb_task = asyncio.create_task(heartbeat_loop(ws))
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("kind") == "req":
                    asyncio.create_task(handle_req(ws, data))
        finally:
            hb_task.cancel()


async def main():
    backoff = BACKOFF_START

    def reset_backoff():
        nonlocal backoff
        backoff = BACKOFF_START

    async with aiohttp.ClientSession() as sess:
        while True:  # NET-2: reconnect forever
            try:
                await session(sess, reset_backoff)
                log("connection closed by server")
            except Exception as e:  # noqa: BLE001
                log(f"link error: {e!r}; retry in ~{backoff:.1f}s")
            await asyncio.sleep(backoff * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, BACKOFF_CAP)


if __name__ == "__main__":
    asyncio.run(main())
