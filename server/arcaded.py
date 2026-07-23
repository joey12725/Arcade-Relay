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

import aiohttp
import yaml
from aiohttp import WSMsgType, web

import jellyfin
import romdl

RPC_TIMEOUT = float(os.environ.get("ARCADED_RPC_TIMEOUT", "15"))
MISSED_BEATS_OFFLINE = 3  # spec §5.2: offline after 3 missed heartbeats
STATE_DIR = os.environ.get("STATE_DIR", "/state")


def git_commit(msg):
    """SRV-2: every state mutation is a git commit in the state repo.

    NB: called from request handlers with NO awaits between the state read
    and this commit — that synchronous window is what makes read-modify-
    write mutations race-free on the single event loop. Adding an await
    inside load->save->commit would reintroduce lost updates.
    """
    subprocess.run(["git", "-C", STATE_DIR, "add", "-A"], capture_output=True)
    r = subprocess.run(["git", "-C", STATE_DIR, "commit", "-qm", msg],
                       capture_output=True, text=True)
    if r.returncode != 0:
        hub.log_event(f"git commit FAILED ({r.returncode}): {msg}: {r.stderr.strip()}")


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


async def cab_screenshot(request):
    import base64
    res = await hub.rpc("system.screenshot", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.Response(body=base64.b64decode(res["result"]["png_b64"]),
                        content_type="image/png")


WEBUI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui.html")


async def webui(request):
    return web.FileResponse(WEBUI_PATH)


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


def _read_yaml(rel):
    p = pathlib.Path(STATE_DIR) / rel
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _write_yaml(rel, data):
    p = pathlib.Path(STATE_DIR) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, default_flow_style=False))


def controls_snapshot():
    """Preset = named snapshot of all mapping layers (CTL-2)."""
    root = pathlib.Path(STATE_DIR) / "controllers"
    snap = {"global": _read_yaml("controllers/global.yaml"), "systems": {}}
    sysdir = root / "systems"
    if sysdir.is_dir():
        for p in sorted(sysdir.glob("*.yaml")):
            snap["systems"][p.stem] = yaml.safe_load(p.read_text()) or {}
    return snap


async def controls_presets(request):
    d = pathlib.Path(STATE_DIR) / "controllers" / "presets"
    names = sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []
    return web.json_response({"presets": names})


async def controls_save_preset(request):
    name = (await request.json())["name"]
    _write_yaml(f"controllers/presets/{name}.yaml", controls_snapshot())
    git_commit(f"arcadectl: controls save-preset {name}")
    hub.log_event(f"preset saved: {name}")
    return web.json_response({"saved": name})


async def controls_load_preset(request):
    name = (await request.json())["name"]
    snap = _read_yaml(f"controllers/presets/{name}.yaml")
    if not snap:
        return web.json_response({"error": f"no preset {name}"}, status=404)
    _write_yaml("controllers/global.yaml", snap.get("global") or {})
    for sysname, data in (snap.get("systems") or {}).items():
        _write_yaml(f"controllers/systems/{sysname}.yaml", data)
    git_commit(f"arcadectl: controls load-preset {name}")
    push_state_soon()
    hub.log_event(f"preset loaded: {name}")
    return web.json_response({"loaded": name})


async def controls_map(request):
    body = await request.json()
    scope, sets = body["scope"], body["set"]
    if scope == "global":
        rel = "controllers/global.yaml"
        data = _read_yaml(rel)
        data.setdefault("buttons", {}).update(sets)
    elif scope.startswith("system:"):
        rel = f"controllers/systems/{scope.split(':', 1)[1]}.yaml"
        data = _read_yaml(rel)
        data.setdefault("remap", {}).update(sets)
    else:
        return web.json_response({"error": f"bad scope {scope}"}, status=400)
    _write_yaml(rel, data)
    git_commit(f"arcadectl: controls map {scope} "
               + " ".join(f"{k}={v}" for k, v in sets.items()))
    push_state_soon()
    hub.log_event(f"controls map {scope}: {sets}")
    return web.json_response(data)


async def controls_monitor(request):
    body = await request.json()
    res = await hub.rpc("input.monitor", {"seconds": body.get("seconds", 2)})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


async def state_rollback(request):
    commit = (await request.json())["commit"]
    r = subprocess.run(["git", "-C", STATE_DIR, "checkout", commit, "--", "."],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return web.json_response({"error": r.stderr.strip()}, status=400)
    git_commit(f"arcadectl: rollback to {commit}")
    push_state_soon()
    hub.log_event(f"state rolled back to {commit}")
    return web.json_response({"rolled_back_to": commit})


SCREENSAVER_REL = "screensaver.yaml"


def load_screensaver():
    d = _read_yaml(SCREENSAVER_REL)
    d.setdefault("idle_seconds", 300)
    d.setdefault("brightness", 80)
    d.setdefault("mode", "shuffle_episodes")
    d.setdefault("sources", [])
    d.setdefault("fallback", "local_clips")
    return d


async def screensaver_sources(request):
    """Every series/movie in Jellyfin, merged with enabled flags (SCR-4)."""
    lib = await jellyfin.items()
    cfg = load_screensaver()
    enabled = {s["jellyfin_id"]: s.get("enabled", False)
               for s in cfg["sources"]}
    return web.json_response([
        {**i, "enabled": enabled.get(i["id"], False)} for i in lib])


async def screensaver_set_source(request):
    body = await request.json()
    sid, enable = body["id"], bool(body["enabled"])
    cfg = load_screensaver()
    for s in cfg["sources"]:
        if s["jellyfin_id"] == sid:
            s["enabled"] = enable
            break
    else:
        cfg["sources"].append({"jellyfin_id": sid, "enabled": enable})
    _write_yaml(SCREENSAVER_REL, cfg)
    git_commit(f"arcadectl: screensaver {'enable' if enable else 'disable'}-source {sid}")
    push_state_soon()
    hub.log_event(f"screensaver source {sid} -> {enable}")
    return web.json_response(cfg)


async def screensaver_playlist(request):
    """Stream URLs (via our /media proxy) for all enabled sources, 480p cap.
    The cabinet reaches these through the same tunnel path as everything
    else (SCR-1)."""
    cfg = load_screensaver()
    out = []
    for s in cfg["sources"]:
        if not s.get("enabled"):
            continue
        for vid in await jellyfin.episodes_of(s["jellyfin_id"]):
            out.append(f"/media/{vid}")
    return web.json_response({"playlist": out,
                              "brightness": cfg["brightness"],
                              "mode": cfg["mode"]})


async def media_proxy(request):
    """Reverse-proxy a transcoded Jellyfin stream so media rides the
    cabinet<->server link (Cloudflare tunnel in prod, toxiproxy in sim)."""
    video_id = request.match_info["video_id"]
    path = await jellyfin.stream_path(video_id)
    resp = web.StreamResponse()
    # total=None is load-bearing: the default 300s total timeout would kill
    # any stream longer than 5 minutes mid-play (Jellyfin transcodes are
    # throttled to near-realtime)
    tmo = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    fwd_headers = {}
    if "Range" in request.headers:
        fwd_headers["Range"] = request.headers["Range"]
    async with aiohttp.ClientSession(timeout=tmo) as sess:
        async with sess.get(jellyfin.JF_URL + path, headers=fwd_headers) as up:
            resp.set_status(up.status)
            if "Content-Type" in up.headers:
                resp.headers["Content-Type"] = up.headers["Content-Type"]
            await resp.prepare(request)
            async for chunk in up.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
    await resp.write_eof()
    return resp


async def screensaver_get(request):
    return web.json_response(load_screensaver())


async def screensaver_set(request):
    body = await request.json()
    cfg = load_screensaver()
    changes = []
    for k in ("brightness", "idle_seconds", "mode", "fallback"):
        if k in body:
            cfg[k] = body[k]
            changes.append(f"{k}={body[k]}")
    _write_yaml(SCREENSAVER_REL, cfg)
    git_commit(f"arcadectl: screensaver set {' '.join(changes)}")
    push_state_soon()
    # live-adjust a running screensaver (brightness <500ms budget)
    if "brightness" in body and hub.ws is not None and not hub.ws.closed:
        try:
            await hub.rpc("screensaver.set", {"brightness": cfg["brightness"]})
        except web.HTTPException:
            pass
    return web.json_response(cfg)


async def screensaver_disable(request):
    body = await request.json()
    res = await hub.rpc("screensaver.disable", {"seconds": body.get("seconds", 0)})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"screensaver suppressed for {body.get('seconds')}s")
    return web.json_response(res["result"])


async def screensaver_cmd(request):
    action = request.match_info["action"]
    if action not in ("on", "off"):
        return web.json_response({"error": "on|off"}, status=400)
    if action == "on":
        pl = json.loads((await screensaver_playlist(request)).text)
        res = await hub.rpc("screensaver.on", pl)
    else:
        res = await hub.rpc("screensaver.off", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"screensaver {action}")
    return web.json_response(res["result"])


# --------------------------------------------------------------- ROM queue

rom_queue = []       # [{id, title, status, detail}]
rom_lock = asyncio.Lock()


def _queue_entry(rid):
    return next((e for e in rom_queue if e["id"] == rid), None)


async def rom_worker(entry):
    """request -> download (ROMDL) -> deliver over the tunnel -> rescan."""
    try:
        entry["status"] = "downloading"
        files = await asyncio.to_thread(romdl.download, entry["id"])
        entry["status"] = "delivering"
        dest = f"/roms/{files['system']}/{files['filename']}"
        res = await hub.rpc("file.fetch", {
            "path": f"/roms/files/{files['filename']}",
            "dest": dest, "sha256": files["sha256"]})
        if not res.get("ok"):
            raise RuntimeError(res.get("error", "delivery failed"))
        art = await hub.rpc("file.fetch", {
            "path": f"/roms/files/{files['filename']}.png",
            "dest": f"/roms/art/{files['filename']}.png"})
        if not art.get("ok"):
            raise RuntimeError("art delivery failed")
        rescan = await hub.rpc("library.rescan", {})
        if not rescan.get("ok"):
            raise RuntimeError("rescan failed")
        entry["status"] = "ready"
        entry["detail"] = dest
        hub.log_event(f"ROM ready: {entry['title']} at {dest}")
    except Exception as e:  # noqa: BLE001
        entry["status"] = "error"
        entry["detail"] = repr(e)
        hub.log_event(f"ROM failed: {entry['title']}: {e!r}")


async def roms_search(request):
    q = request.query.get("q", "")
    return web.json_response(await asyncio.to_thread(romdl.search, q))


async def roms_request(request):
    body = await request.json()
    cands = await asyncio.to_thread(romdl.search, body["query"])
    if not cands:
        return web.json_response({"error": "no match"}, status=404)
    cand = cands[0]
    async with rom_lock:
        entry = _queue_entry(cand["id"])
        if entry is None or entry["status"] == "error":
            if entry:
                rom_queue.remove(entry)
            entry = {"id": cand["id"], "title": cand["title"],
                     "status": "queued", "detail": ""}
            rom_queue.append(entry)
            asyncio.get_event_loop().create_task(rom_worker(entry))
    return web.json_response(entry)


async def roms_status(request):
    return web.json_response({"queue": rom_queue})


async def roms_file(request):
    fname = request.match_info["fname"]
    path = os.path.join(romdl.STAGING, os.path.basename(fname))
    if not os.path.exists(path):
        raise web.HTTPNotFound(text=f"no staged file {fname}")
    return web.FileResponse(path)


AUDIO_REL = "audio.yaml"


def load_audio():
    p = pathlib.Path(STATE_DIR) / AUDIO_REL
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


async def volume_get(request):
    return web.json_response(load_audio())


async def volume_set(request):
    body = await request.json()
    data = load_audio()
    changes = []
    if "master" in body:
        data["master"] = int(body["master"])
        changes.append(f"master={data['master']}")
    if "class" in body:
        cls = body["class"]
        cur = data.setdefault("classes", {}).setdefault(cls, {})
        if "volume" in body:
            cur["volume"] = int(body["volume"])
            changes.append(f"{cls}.volume={cur['volume']}")
        if "muted" in body:
            cur["muted"] = bool(body["muted"])
            changes.append(f"{cls}.muted={cur['muted']}")
    if not changes:
        return web.json_response({"error": "nothing to change"}, status=400)
    p = pathlib.Path(STATE_DIR) / AUDIO_REL
    p.write_text(yaml.safe_dump(data, default_flow_style=False))
    git_commit(f"arcadectl: volume set {' '.join(changes)}")
    push_state_soon()
    hub.log_event(f"volume set: {' '.join(changes)}")
    return web.json_response(data)


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
    web.get("/cab/screenshot", cab_screenshot),
    web.get("/ui", webui),
    web.post("/games/launch", games_launch),
    web.post("/games/kill", games_kill),
    web.get("/state/bundle", state_bundle),
    web.post("/state/push", state_push),
    web.get("/state/log", state_log),
    web.post("/state/rollback", state_rollback),
    web.get("/controls/presets", controls_presets),
    web.post("/controls/save-preset", controls_save_preset),
    web.post("/controls/load-preset", controls_load_preset),
    web.post("/controls/map", controls_map),
    web.post("/controls/monitor", controls_monitor),
    web.get("/screensaver", screensaver_get),
    web.post("/screensaver/set", screensaver_set),
    web.get("/screensaver/sources", screensaver_sources),
    web.post("/screensaver/set-source", screensaver_set_source),
    web.get("/screensaver/playlist", screensaver_playlist),
    web.post("/screensaver/disable", screensaver_disable),
    web.post("/screensaver/{action}", screensaver_cmd),
    web.get("/media/{video_id}", media_proxy),
    web.get("/roms/search", roms_search),
    web.post("/roms/request", roms_request),
    web.get("/roms/status", roms_status),
    web.get("/roms/files/{fname}", roms_file),
    web.get("/volume", volume_get),
    web.post("/volume/set", volume_set),
    web.get("/favorites", favorites_list),
    web.post("/favorites/add", favorites_add),
    web.post("/favorites/remove", favorites_remove),
])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                print=None)
