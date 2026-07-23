#!/usr/bin/env python3
"""arcaded — server control daemon (M0 skeleton).

Hosts the WebSocket hub the cabinet dials into (/cab) and the REST API that
arcadectl / web UI / smart-home consume. Spec §6.1: REST is the only API;
push model and state repo come in later milestones.

Protocol (spec §7): JSON messages, req/res + events.
  cabinet -> server: {"kind":"event","event":"heartbeat","interval":s,"state":{...}}
  server -> cabinet: {"id":uuid,"kind":"req","method":"system.exec","params":{...}}
  cabinet -> server: {"id":uuid,"kind":"res","ok":true,"result":{...}}
"""
import asyncio
import json
import os
import time
import uuid

from aiohttp import WSMsgType, web

RPC_TIMEOUT = float(os.environ.get("ARCADED_RPC_TIMEOUT", "15"))
MISSED_BEATS_OFFLINE = 3  # spec §5.2: offline after 3 missed heartbeats


class Hub:
    def __init__(self):
        self.ws = None                # current cabinet socket (single cabinet, v1)
        self.last_hb = 0.0            # monotonic time of last heartbeat
        self.hb_interval = 5.0        # cabinet-reported heartbeat interval
        self.cab_state = {}           # last heartbeat state payload
        self.pending = {}             # id -> Future for in-flight RPCs
        self.events = []              # (wall_ts, text) presence/event log
        self.connects = 0
        self.beats = 0                # total heartbeats delivered (accept-m0 metric)

    def log_event(self, text):
        self.events.append((time.time(), text))
        self.events = self.events[-500:]

    @property
    def online(self):
        if self.ws is None or self.ws.closed:
            return False
        return (time.monotonic() - self.last_hb) <= self.hb_interval * MISSED_BEATS_OFFLINE

    async def rpc(self, method, params):
        if self.ws is None or self.ws.closed:
            raise web.HTTPServiceUnavailable(text="cabinet offline")
        msg_id = str(uuid.uuid4())
        fut = asyncio.get_event_loop().create_future()
        self.pending[msg_id] = fut
        try:
            await self.ws.send_json(
                {"id": msg_id, "kind": "req", "method": method, "params": params})
            return await asyncio.wait_for(fut, RPC_TIMEOUT)
        except asyncio.TimeoutError:
            raise web.HTTPGatewayTimeout(text=f"cabinet did not answer {method}")
        except ConnectionError:
            raise web.HTTPServiceUnavailable(text="cabinet disconnected mid-request")
        finally:
            self.pending.pop(msg_id, None)


hub = Hub()


async def cab_ws(request):
    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)
    if hub.ws is not None and not hub.ws.closed:
        await hub.ws.close()  # newest connection wins (reconnect race)
    hub.ws = ws
    hub.connects += 1
    hub.last_hb = time.monotonic()
    hub.log_event("cabinet connected")
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = json.loads(msg.data)
            if data.get("kind") == "event" and data.get("event") == "heartbeat":
                hub.beats += 1
                hub.last_hb = time.monotonic()
                hub.hb_interval = float(data.get("interval", hub.hb_interval))
                hub.cab_state = data.get("state", {})
            elif data.get("kind") == "res":
                fut = hub.pending.get(data.get("id"))
                if fut is not None and not fut.done():
                    fut.set_result(data)
    finally:
        if hub.ws is ws:
            hub.ws = None
            # fail in-flight RPCs immediately instead of letting them ride
            # out the 15s timeout — callers get 503 now
            for fut in list(hub.pending.values()):
                if not fut.done():
                    fut.set_exception(ConnectionError("cabinet disconnected"))
        hub.log_event("cabinet disconnected")
    return ws


async def root(request):
    return web.Response(text="arcaded\n")


async def health(request):
    return web.json_response({"ok": True})


async def status(request):
    return web.json_response({
        "online": hub.online,
        "connects": hub.connects,
        "beats": hub.beats,
        "heartbeat_age_s": round(time.monotonic() - hub.last_hb, 3) if hub.connects else None,
        "heartbeat_interval_s": hub.hb_interval,
        "cabinet_state": hub.cab_state,
    })


async def events(request):
    return web.json_response(
        [{"ts": ts, "event": e} for ts, e in hub.events[-50:]])


async def cab_exec(request):
    body = await request.json()
    res = await hub.rpc("system.exec", {"cmd": body["cmd"]})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


app = web.Application()
app.add_routes([
    web.get("/", root),
    web.get("/health", health),
    web.get("/status", status),
    web.get("/events", events),
    web.get("/cab", cab_ws),
    web.post("/cab/exec", cab_exec),
])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                print=None)
