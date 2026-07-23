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
import pathlib
import subprocess
import time
import uuid

import yaml
from aiohttp import WSMsgType, web

RPC_TIMEOUT = float(os.environ.get("ARCADED_RPC_TIMEOUT", "15"))
MISSED_BEATS_OFFLINE = 3  # spec §5.2: offline after 3 missed heartbeats
STATE_DIR = os.environ.get("STATE_DIR", "/state")


def git_commit(msg):
    """SRV-2: every state mutation is a git commit in the state repo."""
    subprocess.run(["git", "-C", STATE_DIR, "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", STATE_DIR, "commit", "-qm", msg],
                   capture_output=True)


FAVORITES_REL = "library/favorites.yaml"


def load_favorites():
    p = pathlib.Path(STATE_DIR) / FAVORITES_REL
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or {}
    return data.get("favorites") or []


def save_favorites(favs, msg):
    p = pathlib.Path(STATE_DIR) / FAVORITES_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"favorites": sorted(favs)},
                                default_flow_style=False))
    git_commit(msg)


def push_state_soon():
    """Fire-and-forget push after a state change; a cabinet that's offline
    gets the full bundle on reconnect anyway (push-on-connect)."""
    async def _push():
        try:
            await hub.rpc("state.sync", {"bundle": build_bundle()})
            hub.log_event("state pushed after change")
        except web.HTTPException as e:
            hub.log_event(f"state push deferred (cabinet offline?): {e.reason}")
    asyncio.get_event_loop().create_task(_push())


def build_bundle():
    """Parse every YAML in the state repo into {relpath: data} (SRV-2)."""
    bundle = {}
    root = pathlib.Path(STATE_DIR)
    if root.is_dir():
        for p in sorted(root.rglob("*.yaml")):
            bundle[str(p.relative_to(root))] = yaml.safe_load(p.read_text())
    return bundle


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

    async def push_state():
        try:
            res = await hub.rpc("state.sync", {"bundle": build_bundle()})
            hub.log_event(f"state pushed on connect: {res.get('result')}")
        except web.HTTPException as e:
            hub.log_event(f"state push on connect failed: {e.reason}")
    asyncio.get_event_loop().create_task(push_state())
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


async def games_launch(request):
    body = await request.json()
    res = await hub.rpc("game.launch", {"system": body["system"], "path": body["path"],
                                        "args": body.get("args", [])})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"game launched: {res['result'].get('running')}")
    return web.json_response(res["result"])


async def state_bundle(request):
    return web.json_response(build_bundle())


async def state_push(request):
    res = await hub.rpc("state.sync", {"bundle": build_bundle()})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event("state pushed (manual)")
    return web.json_response(res["result"])


async def favorites_list(request):
    return web.json_response({"favorites": load_favorites()})


async def favorites_add(request):
    body = await request.json()
    fav_id = body["id"]
    favs = load_favorites()
    if fav_id not in favs:
        favs.append(fav_id)
        save_favorites(favs, f"arcadectl: favorite add {fav_id}")
        push_state_soon()
        hub.log_event(f"favorite added: {fav_id}")
    return web.json_response({"favorites": sorted(favs)})


async def favorites_remove(request):
    body = await request.json()
    fav_id = body["id"]
    favs = load_favorites()
    if fav_id in favs:
        favs.remove(fav_id)
        save_favorites(favs, f"arcadectl: favorite remove {fav_id}")
        push_state_soon()
        hub.log_event(f"favorite removed: {fav_id}")
    return web.json_response({"favorites": sorted(favs)})


async def state_log(request):
    r = subprocess.run(["git", "-C", STATE_DIR, "log", "--oneline", "-20"],
                       capture_output=True, text=True)
    return web.json_response({"log": r.stdout.splitlines()})


async def games_kill(request):
    res = await hub.rpc("game.kill", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event("game killed")
    return web.json_response(res["result"])


app = web.Application()
app.add_routes([
    web.get("/", root),
    web.get("/health", health),
    web.get("/status", status),
    web.get("/events", events),
    web.get("/cab", cab_ws),
    web.post("/cab/exec", cab_exec),
    web.post("/games/launch", games_launch),
    web.post("/games/kill", games_kill),
    web.get("/state/bundle", state_bundle),
    web.post("/state/push", state_push),
    web.get("/state/log", state_log),
    web.get("/favorites", favorites_list),
    web.post("/favorites/add", favorites_add),
    web.post("/favorites/remove", favorites_remove),
])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                print=None)
