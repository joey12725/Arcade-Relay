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
import hashlib
import json
import os
import pathlib
import random
import re
import subprocess
import tempfile
import time
import urllib.parse
import uuid

import aiohttp
import yaml
from aiohttp import WSMsgType, web

import artdb
import integrations
import plex
import romdl
import shuffle
import subtitles

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

    async def rpc(self, method, params, timeout=None):
        if self.ws is None or self.ws.closed:
            raise web.HTTPServiceUnavailable(text="cabinet offline")
        msg_id = str(uuid.uuid4())
        fut = asyncio.get_event_loop().create_future()
        self.pending[msg_id] = fut
        try:
            await self.ws.send_json(
                {"id": msg_id, "kind": "req", "method": method, "params": params})
            return await asyncio.wait_for(fut, timeout or RPC_TIMEOUT)
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
                # Discord / Home Assistant ride the heartbeat rather than
                # polling; a slow publisher must not stall the socket read.
                asyncio.get_event_loop().create_task(_publish_presence())
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
    res = await hub.rpc("system.exec", {"cmd": body["cmd"]}, timeout=45)
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


async def cab_lifeline(request):
    action = request.match_info["action"]
    if action not in ("on", "off", "restart-network"):
        return web.json_response({"error": "on|off|restart-network"},
                                 status=400)
    method = ("lifeline.restart_network" if action == "restart-network"
              else f"lifeline.{action}")
    res = await hub.rpc(method, {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"lifeline {action}")
    return web.json_response(res["result"])


# ---- save backup receiver: the cabinet POSTs a tar.gz of /saves whenever
# its content-sha changes. Stored outside the state repo (binary blobs);
# latest.tar.gz is replaced atomically, meta.json records provenance.
SAVES_BACKUP_DIR = os.environ.get("SAVES_BACKUP_DIR", "/saves-backup")


async def saves_upload(request):
    blob = await request.read()
    if not blob:
        return web.json_response({"error": "empty body"}, status=400)
    sha = request.headers.get("X-Saves-Sha256", "")
    os.makedirs(SAVES_BACKUP_DIR, exist_ok=True)
    tar_path = os.path.join(SAVES_BACKUP_DIR, "latest.tar.gz")
    with open(tar_path + ".part", "wb") as f:
        f.write(blob)
    os.replace(tar_path + ".part", tar_path)
    meta = {"received": True, "sha": sha, "bytes": len(blob),
            "received_at": time.time()}
    with open(os.path.join(SAVES_BACKUP_DIR, "meta.json.part"), "w") as f:
        json.dump(meta, f)
    os.replace(os.path.join(SAVES_BACKUP_DIR, "meta.json.part"),
               os.path.join(SAVES_BACKUP_DIR, "meta.json"))
    hub.log_event(f"saves backup received: {len(blob)}B sha {sha[:12]}")
    return web.json_response(meta)


async def saves_status(request):
    try:
        with open(os.path.join(SAVES_BACKUP_DIR, "meta.json")) as f:
            return web.json_response(json.load(f))
    except FileNotFoundError:
        return web.json_response({"received": False})


# ---- remote view (spec §5.10): on-demand VNC tunneled through the server
# connection. The server listens on VNC_PORT; each connecting VNC client is
# paired (by token) with a WS the cabinet dials back to /cab/vnc-tunnel, and
# bytes are piped both ways to the cabinet's localhost-only x11vnc.
VNC_PORT = int(os.environ.get("VNC_PORT", "5901"))
vnc = {"server": None, "waiting": {}}


async def _vnc_client(reader, writer):
    token = str(uuid.uuid4())
    fut = asyncio.get_event_loop().create_future()
    vnc["waiting"][token] = (fut, reader, writer)
    try:
        await hub.rpc("vnc.connect", {"token": token})
        await asyncio.wait_for(fut, 15)   # tunnel WS arrived; it owns the pipe
    except Exception:  # noqa: BLE001 — cabinet offline / tunnel never dialed
        vnc["waiting"].pop(token, None)
        writer.close()
        hub.log_event("remote view: client dropped (no tunnel)")


async def cab_remote_view(request):
    body = await request.json() if request.can_read_body else {}
    if body.get("on", True):
        res = await hub.rpc("remote_view.on", {})
        if not res.get("ok"):
            return web.json_response(res, status=500)
        if vnc["server"] is None:
            vnc["server"] = await asyncio.start_server(
                _vnc_client, "0.0.0.0", VNC_PORT)
        hub.log_event(f"remote view ON (vnc port {VNC_PORT})")
        return web.json_response({"remote_view": True, "port": VNC_PORT,
                                  "url": f"vnc://<server-host>:{VNC_PORT}"})
    res = await hub.rpc("remote_view.off", {})
    if vnc["server"] is not None:
        vnc["server"].close()
        vnc["server"] = None
    hub.log_event("remote view OFF")
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response({"remote_view": False})


async def vnc_tunnel_ws(request):
    entry = vnc["waiting"].pop(request.query.get("token", ""), None)
    if entry is None:
        raise web.HTTPForbidden(text="unknown tunnel token")
    fut, reader, writer = entry
    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)
    if not fut.done():
        fut.set_result(True)

    async def tcp_to_ws():
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await ws.send_bytes(data)

    async def ws_to_tcp():
        async for msg in ws:
            if msg.type != WSMsgType.BINARY:
                break
            writer.write(msg.data)
            await writer.drain()

    done, pending = await asyncio.wait(
        [asyncio.create_task(tcp_to_ws()), asyncio.create_task(ws_to_tcp())],
        return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    writer.close()
    await ws.close()
    return ws


async def cab_input(request):
    body = await request.json()
    # type-check here: a non-string reaching xdotool surfaces as a raw
    # TypeError 500 from the cabinet instead of a usable 400
    for field, method in (("key", "input.press"), ("text", "input.text")):
        if field in body:
            if not isinstance(body[field], str) or not body[field]:
                return web.json_response(
                    {"error": f"{field} must be a non-empty string"},
                    status=400)
            res = await hub.rpc(method, {field: body[field]})
            break
    else:
        return web.json_response({"error": "need key or text"}, status=400)
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"synthetic input: {body.get('key') or 'text'}")
    return web.json_response(res["result"])


async def cab_health(request):
    res = await hub.rpc("system.health", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


async def cab_clear_cache(request):
    res = await hub.rpc("system.clear_cache", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"cache cleared: {res['result'].get('freed_h')}")
    return web.json_response(res["result"])


async def cab_reboot(request):
    res = await hub.rpc("system.reboot", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event("cabinet reboot requested")
    return web.json_response(res["result"])


async def cab_restart_frontend(request):
    res = await hub.rpc("frontend.restart", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event("frontend restart requested")
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
    snap = {"global": _read_yaml("controllers/global.yaml"), "systems": {},
            "games": _read_yaml("controllers/games.yaml")}
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
    # A preset saved before the per-game layer existed has no "games" key —
    # restoring {} would silently wipe per-game maps it never captured. Only
    # presets that actually snapshotted the layer may replace it.
    if "games" in snap:
        _write_yaml("controllers/games.yaml", snap["games"] or {})
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
    elif scope.startswith("game:"):
        # Layer 3 (spec §5.7): all per-game overrides live in one file keyed
        # by '<system>/<rom>', so the bundle stays small and diffable.
        gid = scope.split(":", 1)[1]
        sysname, _, rom = gid.partition("/")
        # exactly '<system>/<rom-basename>' — the id is a bundle key, and a
        # junk key (traversal-looking or nested) would silently never match
        # what the renderer computes from the launched rom path
        if (not sysname or not rom or "/" in rom
                or ".." in gid or gid != gid.strip()):
            return web.json_response(
                {"error": f"game scope needs <system>/<rom>, got {gid!r}"},
                status=400)
        rel = "controllers/games.yaml"
        data = _read_yaml(rel)
        data.setdefault("games", {}).setdefault(gid, {}) \
            .setdefault("remap", {}).update(sets)
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


screensaver_aspect_modes = ("crop", "bars", "stretch")
SCREENSAVER_REL = "screensaver.yaml"


def load_screensaver():
    d = _read_yaml(SCREENSAVER_REL)
    d.setdefault("idle_seconds", 300)
    d.setdefault("brightness", 80)
    d.setdefault("mode", "shuffle_episodes")
    d.setdefault("sources", [])
    d.setdefault("fallback", "local_clips")
    # 4:3 panel handling for 16:9 source (crop keeps today's behavior)
    d.setdefault("aspect", "crop")
    # trim titles/credits off each item
    d.setdefault("skip_intro_s", 0)
    d.setdefault("skip_outro_s", 0)
    # queue ordering (server/shuffle.py) — `mode` above is the legacy
    # two-value key the spec named; `shuffle` is the richer policy set and
    # wins when both are present
    d.setdefault("shuffle", shuffle.DEFAULT_POLICY)
    d.setdefault("movies_as_series", True)
    # English subtitles on the screensaver: one switch, no track picking —
    # media_proxy chooses the track (server/subtitles.py), the cabinet
    # shows or hides it
    d.setdefault("subtitles", False)
    return d


def source_id(s):
    """Sources were keyed by `jellyfin_id` before the Plex migration; accept
    either so an existing state repo keeps working."""
    return s.get("plex_id") or s.get("jellyfin_id")


async def screensaver_sources(request):
    """Every series/movie in Jellyfin, merged with enabled flags (SCR-4)."""
    lib = await plex.items()
    cfg = load_screensaver()
    enabled = {source_id(s): s.get("enabled", False)
               for s in cfg["sources"]}
    return web.json_response([
        {**i, "enabled": enabled.get(i["id"], False)} for i in lib])


async def thumb_proxy(request):
    """Plex poster for a source (spec §6.3 thumbnails). Proxied so the
    browser needs no Plex credentials or reachability."""
    got = await plex.image(request.match_info["item_id"])
    if got is None:
        raise web.HTTPNotFound(text="no image for this item")
    body, ctype = got
    return web.Response(body=body, content_type=ctype.split(";")[0],
                        headers={"Cache-Control": "max-age=3600"})


async def screensaver_set_source(request):
    body = await request.json()
    sid, enable = body["id"], bool(body["enabled"])
    cfg = load_screensaver()
    for s in cfg["sources"]:
        if source_id(s) == sid:
            s["enabled"] = enable
            break
    else:
        cfg["sources"].append({"plex_id": sid, "enabled": enable})
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
    lib = {i["id"]: i for i in (await plex.items())}
    entries = []
    for si, src in enumerate(cfg["sources"]):
        if not src.get("enabled"):
            continue
        sid = source_id(src)
        meta = lib.get(sid) or {}
        for idx, vid in enumerate(await plex.episodes_of(sid)):
            entries.append({"id": f"/media/{vid}", "source_id": sid,
                            "source_type": meta.get("type", "Series"),
                            "index": idx, "source_index": si})
    # Ordering policy (server/shuffle.py). The legacy `mode` key maps onto
    # the new policy set so an old state repo keeps its behavior.
    policy = cfg.get("shuffle") or shuffle.DEFAULT_POLICY
    if cfg.get("mode") == "continue_series" and "shuffle" not in cfg:
        policy = "in_order"
    out = shuffle.order(entries, policy,
                        movies_as_series=bool(cfg.get("movies_as_series", True)))
    return web.json_response({"playlist": out,
                              "brightness": cfg["brightness"],
                              "mode": cfg["mode"],
                              "shuffle": policy,
                              "movies_as_series": bool(cfg.get("movies_as_series", True)),
                              "aspect": cfg.get("aspect", "crop"),
                              "skip_intro_s": int(cfg.get("skip_intro_s", 0)),
                              "skip_outro_s": int(cfg.get("skip_outro_s", 0)),
                              "subtitles": bool(cfg.get("subtitles", False)),
                              "fallback": cfg.get("fallback", "local_clips")})


FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
MEDIA_MAX_HEIGHT = int(os.environ.get("SCREENSAVER_MAX_HEIGHT", "480"))
MEDIA_BITRATE_K = int(os.environ.get("SCREENSAVER_VIDEO_BITRATE", "1500000")) // 1000


def media_argv(src, streams=(), sub=None, sub_file=None, audio_lang=None):
    """ffmpeg argv for one screensaver item.

    Without a subtitle pick, ffmpeg chooses video and audio itself and
    `-sn` keeps every subtitle out: left alone it grabs the first text track
    in any language, keeps its default flag, and mpv shows it. With a pick, every stream is
    mapped explicitly and the audio is the one ffmpeg would have chosen
    (subtitles.pick_audio), kept to `audio_lang` when given. `sub_file` is
    a local copy of a sidecar; an embedded pick is mapped straight out of
    the source.
    """
    argv = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", src]
    if sub is not None:
        if sub_file:
            argv += ["-i", sub_file]
        audio = subtitles.pick_audio(streams, audio_lang)
        argv += ["-map", "0:V:0",
                 "-map", f"0:{audio['index']}" if audio else "0:a:0?",
                 "-map", "1:0" if sub_file else f"0:{sub['index']}"]
    argv += [
        "-vf", f"scale=-2:{MEDIA_MAX_HEIGHT}",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{MEDIA_BITRATE_K}k", "-maxrate", f"{MEDIA_BITRATE_K}k",
        "-bufsize", f"{MEDIA_BITRATE_K * 2}k",
        "-c:a", "aac", "-b:a", "96k",
    ]
    if sub is None:
        argv.append("-sn")
    else:
        # No default/forced flag (-disposition 0 clears the source's): the
        # cabinet selects it by language (--slang=eng) and decides its
        # visibility, and a cabinet still on the old mpv flags selects
        # nothing rather than showing subtitles nobody turned on.
        # A subtitle track with nothing to say yet (signs-only, first sign
        # 20 s in) would otherwise make the muxer hold back up to 10 s of
        # audio and video waiting for it.
        argv += [*subtitles.codec_args(sub),
                 "-metadata:s:s:0", "language=eng",
                 "-disposition:s:0", "0",
                 "-max_interleave_delta", "1000000"]
    argv += ["-f", "matroska", "-live", "1", "pipe:1"]
    return argv


async def _sidecar_file(sub):
    """Fetch a sidecar subtitle to a temp file (None on any failure).

    Fetched up front rather than handed to ffmpeg as a URL: a second network
    input that fails takes the whole stream down with it, and a missing
    subtitle must never cost the episode."""
    try:
        data = subtitles.to_utf8(await plex.fetch(sub["key"]))
    except Exception as e:  # noqa: BLE001
        print(f"media: sidecar {sub.get('key')} unavailable: {e!r}", flush=True)
        return None
    fd, path = tempfile.mkstemp(
        prefix="arcade-sub-",
        suffix="." + subtitles.SIDECAR[subtitles._codec(sub)])
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


async def media_proxy(request):
    """Serve a 480p/1.5Mbps transcode of a library item to the cabinet.

    arcaded owns this transcode rather than asking Plex for one. Two reasons:
    Plex's universal transcoder needs a per-session `decision` handshake AND
    downloads codecs from codecs.plex.tv on first use, so a server without
    internet egress fails at the sub-playlist with a 500; and doing it here
    means the tunnel bandwidth cap (the whole point of SCR-1's 480p rule) is
    enforced by us instead of negotiated with someone else's server.

    Output is fragmented matroska so it streams without a seekable output —
    mpv plays it straight from the pipe.

    Every item carries its best English subtitle track when it has one
    (server/subtitles.py), whatever the `subtitles` setting says: the
    cabinet shows or hides it, so the switch acts on the episode already
    playing and cached clips follow it too. Only with subtitles ON may a
    better-subtitled version of the episode replace the first one, and it
    then keeps the first one's audio language.
    """
    video_id = request.match_info["video_id"]
    versions = await plex.versions(video_id)
    original = versions[0]
    version, sub = original, None
    try:
        if load_screensaver().get("subtitles"):
            version, sub = subtitles.choose(versions)
        else:
            sub = subtitles.pick(original["streams"])
    except Exception as e:  # noqa: BLE001 — never let a subtitle cost the episode
        print(f"media: {video_id}: no subtitle, choice failed: {e!r}", flush=True)
        version, sub = original, None
    sub_file = None
    if sub is not None and subtitles.is_external(sub):
        sub_file = await _sidecar_file(sub)
        if sub_file is None:
            version, sub = original, None   # the switch was for that track
    audio_lang = None
    if version is not original:
        first_audio = subtitles.pick_audio(original["streams"])
        audio_lang = first_audio and first_audio.get("languageCode")

    async def spawn(version, sub):
        argv = media_argv(plex.PLEX_URL + version["part"], version["streams"],
                          sub, sub_file if sub else None, audio_lang)
        return await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL)

    async def head(proc):
        """The first 64 KiB of output, or all of it if ffmpeg exits first."""
        buf = b""
        while len(buf) < 64 * 1024:
            chunk = await proc.stdout.read(64 * 1024 - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    proc = await spawn(version, sub)
    resp = web.StreamResponse()
    hung_up = False
    resp.headers["Content-Type"] = "video/x-matroska"
    try:
        first = await head(proc)
        if len(first) < 64 * 1024 and sub is not None \
                and await proc.wait() != 0:
            # ffmpeg gave up at the start (a muxer refusing a track does so
            # while writing the header, after a few hundred bytes): the
            # explicit mapping or the track did not survive contact. Nothing
            # has gone to the cabinet yet, so play the original without.
            print(f"media: {video_id} failed with subtitle track "
                  f"{sub.get('id')} ({sub.get('codec')}); retrying without",
                  flush=True)
            proc = await spawn(original, None)
            first = await head(proc)
        await resp.prepare(request)
        chunk = first
        while chunk:
            await resp.write(chunk)
            chunk = await proc.stdout.read(64 * 1024)
    except (ConnectionResetError, asyncio.CancelledError):
        hung_up = True    # cabinet hung up (playlist advanced) — normal
    finally:
        # never leak an ffmpeg: the screensaver advances constantly
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if sub_file:
            os.unlink(sub_file)
    if resp.prepared and not hung_up:
        await resp.write_eof()    # to a closed socket it raises, every advance
    return resp


async def screensaver_get(request):
    return web.json_response(load_screensaver())


async def screensaver_set(request):
    body = await request.json()
    cfg = load_screensaver()
    # reject values nothing implements: committing `fallback: dimclock`
    # would look like it worked and silently do nothing at idle
    for k, allowed in (("mode", ("shuffle_episodes", "continue_series")),
                       ("fallback", ("local_clips", "dim_clock", "off")),
                       ("aspect", screensaver_aspect_modes),
                       ("shuffle", shuffle.POLICIES)):
        if k in body and body[k] not in allowed:
            return web.json_response(
                {"error": f"{k} must be one of {', '.join(allowed)}"},
                status=400)
    for k in ("skip_intro_s", "skip_outro_s"):
        if k in body:
            try:
                if int(body[k]) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return web.json_response(
                    {"error": f"{k} must be a non-negative integer"},
                    status=400)
    # a strict bool: "false" from a hand-rolled client is truthy and would
    # commit the opposite of what was asked
    if "subtitles" in body and not isinstance(body["subtitles"], bool):
        return web.json_response(
            {"error": "subtitles must be true or false"}, status=400)
    changes = []
    for k in ("brightness", "idle_seconds", "mode", "fallback", "aspect",
              "shuffle", "movies_as_series", "skip_intro_s", "skip_outro_s",
              "subtitles"):
        if k in body:
            cfg[k] = body[k]
            changes.append(f"{k}={body[k]}")
    _write_yaml(SCREENSAVER_REL, cfg)
    git_commit(f"arcadectl: screensaver set {' '.join(changes)}")
    push_state_soon()
    # live-adjust a running screensaver (brightness <500ms budget; subtitles
    # show/hide on the episode already playing, not just the next one)
    live = {k: cfg[k] for k in ("brightness", "subtitles") if k in body}
    if live and hub.ws is not None and not hub.ws.closed:
        try:
            res = await hub.rpc("screensaver.set", live)
            if not res.get("ok"):
                # committed either way; say so instead of claiming it showed
                hub.log_event("screensaver: cabinet did not apply "
                              f"{live} live: {res.get('error')}")
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
        if not pl.get("playlist"):
            # No enabled sources. The cabinet's idle path falls back to its
            # local clip cache here, so forcing it on must too — otherwise
            # the same state gives attract mode on idle but an error from
            # the button, which is just confusing. cabd reports whether the
            # fallback actually had clips.
            res = await hub.rpc("screensaver.on_fallback", pl)
            if res.get("ok") and not res["result"].get("screensaver"):
                # cabd says WHY it declined (game running, lifeline up,
                # fallback disabled, no clips); relaying its own note beats
                # a canned "enable a source" that may fix nothing.
                note = res["result"].get("note", "")
                hint = ("no enabled screensaver sources and no cached clips"
                        if note == "no cached clips" else
                        f"screensaver not started: {note}")
                if note in ("no cached clips", ""):
                    hint += (" — enable one with `arcadectl screensaver "
                             "enable-source <id>`")
                return web.json_response({"error": hint}, status=400)
        else:
            res = await hub.rpc("screensaver.on", pl)
    else:
        res = await hub.rpc("screensaver.off", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"screensaver {action}")
    return web.json_response(res["result"])


# --------------------------------------------------------------- ROM queue

rom_queue = []       # [{id, title, status, detail, bytes_done?, bytes_total?}]
rom_lock = asyncio.Lock()
# id -> threading.Event for transfers in flight; set by /roms/cancel, checked
# by romdl.download between chunks. Lives outside the queue entry because the
# entry is JSON-serialized to the UI.
rom_cancel_events = {}
rom_stopping = False
rom_slots = asyncio.Semaphore(2)
ROM_QUEUE_PATH = pathlib.Path(os.environ.get("ROM_QUEUE_PATH", os.path.join(romdl.STAGING, "queue.json")))


def save_rom_queue():
    ROM_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROM_QUEUE_PATH.with_suffix(".part")
    tmp.write_text(json.dumps(rom_queue))
    os.replace(tmp, ROM_QUEUE_PATH)


def _queue_entry(rid):
    return next((e for e in rom_queue if e["id"] == rid), None)


def _rom_system(system):
    if not romdl.using_real():
        return system
    console = romdl.SYSTEM_TO_CONSOLE.get((system or "").lower())
    canonical = romdl.CONSOLE_TO_SYSTEM.get(console)
    cfg = yaml.safe_load((pathlib.Path(STATE_DIR) / "cabinet.yaml").read_text()) or {}
    if canonical not in cfg.get("systems", []) or not cfg.get("emulators", {}).get(canonical, {}).get("core"):
        raise ValueError(f"system {system!r} has no configured cabinet emulator")
    return canonical


def _installed_roms():
    p = pathlib.Path(STATE_DIR) / "library/downloads.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def _register_installed_rom(entry):
    if not entry.get("path"):
        return
    installed = _installed_roms()
    gid = entry["system"] + "/" + pathlib.Path(entry["path"]).name
    value = {"title": entry.get("display_title") or pathlib.Path(entry["title"]).stem,
             "sha256": entry["sha256"],
             "files": [f["filename"] for f in entry.get("files", [])]}
    if installed.get(gid) == value:
        return
    installed[gid] = value
    p = pathlib.Path(STATE_DIR) / "library/downloads.yaml"
    p.parent.mkdir(exist_ok=True)
    p.write_text(yaml.safe_dump(installed))
    git_commit(f"arcadectl: register installed ROM {gid}")


async def _deliver_art(files, dest_rom):
    """Put cover art next to a freshly delivered ROM. Never fatal.

    Two sources, in order: art the backend staged itself (the fake backend
    does; real ROMDL has no artwork), then artdb's libretro-thumbnails
    lookup. A game with no cover is still a perfectly good game, so a miss
    here must not fail the request the way it used to.
    """
    stem = os.path.splitext(files["filename"])[0]
    try:
        if files.get("art_path"):
            res = await hub.rpc("file.fetch", {
                "path": f"/roms/files/{urllib.parse.quote(files['filename'])}.png",
                "dest": f"/roms/art/{files['filename']}.png"})
            return bool(res.get("ok"))
        if await artdb.art(files["system"], files["filename"]) is None:
            hub.log_event(f"no cover art found for {files['title']}")
            return False
        res = await hub.rpc("file.fetch", {
            "path": f"/art/{files['system']}/{urllib.parse.quote(files['filename'])}",
            "dest": f"/roms/art/{stem}.png"})
        return bool(res.get("ok"))
    except (web.HTTPException, OSError, RuntimeError) as e:
        hub.log_event(f"art delivery skipped for {files['title']}: {e!r}")
        return False


async def rom_worker(entry):
    async with rom_slots:
        if entry not in rom_queue:
            return
        await _rom_worker(entry)


async def _rom_worker(entry):
    """request -> download (ROMDL) -> deliver over the tunnel -> rescan."""
    import threading
    cancel = rom_cancel_events.setdefault(entry["id"], threading.Event())

    def progress(done, total):
        entry["bytes_done"] = done
        entry["bytes_total"] = total

    try:
        for _ in range(60):
            if hub.online:
                break
            await asyncio.sleep(1)
        if entry.get("query") and not entry.get("candidate_id"):
            entry["status"] = "searching"
            save_rom_queue()
            cands = await asyncio.to_thread(romdl.search, entry["query"], entry["system"])
            if not cands:
                raise RuntimeError("no match")
            if romdl.using_real() and cands[0].get("score", 0) < 100:
                raise RuntimeError("no high-confidence title match; search and select an exact candidate")
            entry["candidate_id"] = cands[0]["id"]
            entry["title"] = cands[0]["title"]
        if cancel.is_set():
            raise romdl.Cancelled("canceled before download")
        entry["status"] = "downloading"
        save_rom_queue()
        files = await asyncio.to_thread(
            romdl.download, entry.get("candidate_id", entry["id"]), cancel, progress)
        entry["status"] = "delivering"
        entry["title"] = files["title"]
        entry["system"] = files["system"]
        dest = f"/roms/{files['system']}/{files['filename']}"
        save_rom_queue()
        payloads = files.get("files") or [files]
        # Install the launch descriptor last, after all disc tracks exist.
        payloads = sorted(payloads, key=lambda f: f["filename"] == files["filename"])
        for f in payloads:
            staged = os.path.relpath(f["rom_path"], romdl.STAGING)
            res = await hub.rpc("file.fetch", {
                "path": f"/roms/files/{urllib.parse.quote(staged)}",
                "dest": f"/roms/{files['system']}/{f['filename']}", "sha256": f["sha256"],
                "protect_existing": True}, timeout=3600)
            if not res.get("ok"):
                raise RuntimeError(res.get("error", "delivery failed"))
        arted = await _deliver_art(files, dest)
        entry["path"] = dest
        entry["sha256"] = files["sha256"]
        entry["files"] = [{"filename": f["filename"], "sha256": f["sha256"]} for f in payloads]
        entry["source_url"] = files.get("source_url")
        entry["source_bytes"] = files.get("bytes")
        entry["source_metadata"] = files.get("source_metadata")
        entry["source_reused"] = files.get("source_reused", False)
        entry["display_title"] = files.get("display_title")
        _register_installed_rom(entry)
        synced = await hub.rpc("state.sync", {"bundle": build_bundle()}, timeout=45)
        if not synced.get("ok"):
            raise RuntimeError("installed game metadata sync failed")
        rescan = await hub.rpc("library.rescan", {})
        if not rescan.get("ok"):
            raise RuntimeError("rescan failed")
        entry["status"] = "ready"
        entry["detail"] = dest + ("" if arted else "  (no cover art)")
        hub.log_event(f"ROM ready: {entry['title']} at {dest}")
    except romdl.Cancelled as e:
        entry["status"] = "queued" if rom_stopping else "canceled"
        entry["detail"] = "resume after server restart" if rom_stopping else str(e)
        hub.log_event(f"ROM canceled: {entry['title']} ({e})")
    except Exception as e:  # noqa: BLE001
        entry["status"] = "error"
        entry["detail"] = repr(e)
        hub.log_event(f"ROM failed: {entry['title']}: {e!r}")
    finally:
        rom_cancel_events.pop(entry["id"], None)
        save_rom_queue()


async def roms_search(request):
    """Candidates for a query. `system` is required by the real ROMDL backend
    (it searches one console at a time); the fake ignores it."""
    try:
        res = await asyncio.to_thread(
            romdl.search, request.query.get("q", ""),
            _rom_system(request.query.get("system")),
            min(int(request.query.get("limit", 10)), 50),
            request.query.get("deep") in ("1", "true"))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return web.json_response({"error": f"romdl search failed: {e}"},
                                 status=502)
    # An empty result from a console with no live source is not a bad query;
    # say which, so the UI does not tell someone to try different spelling.
    note = None
    if not res and romdl.using_real():
        note = romdl.coverage(request.query.get("system"))
    return web.json_response({"results": res, "note": note})


async def roms_backend(request):
    """Which backend is live and which systems it can actually reach."""
    cfg = yaml.safe_load((pathlib.Path(STATE_DIR) / "cabinet.yaml").read_text()) or {}
    systems = sorted(s for s in cfg.get("systems", []) if s in romdl.SYSTEM_TO_CONSOLE and cfg.get("emulators", {}).get(s, {}).get("core"))
    return web.json_response({
        "backend": romdl.backend_name(),
        "systems": systems,
        "coverage": ({s: romdl.coverage(s) for s in systems}
                     if romdl.using_real() else {}),
        "dead_sources": romdl.DEAD_SOURCES if romdl.using_real() else {},
    })


async def roms_request(request):
    body = await request.json()
    if romdl.using_real():
        try:
            body["system"] = _rom_system(body.get("system"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
    # An explicit id skips the search entirely — that is how the UI queues a
    # specific candidate the user picked out of a result list, rather than
    # trusting "best match" a second time.
    if body.get("id"):
        cand = {"id": body["id"], "title": body.get("title") or body["id"]}
    else:
        if not body.get("query") or (romdl.using_real() and not body.get("system")):
            return web.json_response({"error": "query and system are required"}, status=400)
        key = (body.get("system", "") + ":" + body["query"].strip().lower()).encode()
        cand = {"id": hashlib.sha256(key).hexdigest()[:16], "title": body["query"]}
    async with rom_lock:
        entry = _queue_entry(cand["id"])
        if entry is None or entry["status"] in ("error", "canceled"):
            if entry:
                rom_queue.remove(entry)
            entry = {"id": cand["id"], "title": cand["title"],
                     "system": cand.get("system") or body.get("system") or "",
                     "status": "queued", "detail": ""}
            rom_queue.append(entry)
            if not body.get("id"):
                entry["query"] = body["query"]
            save_rom_queue()
            asyncio.get_event_loop().create_task(rom_worker(entry))
    return web.json_response(entry)


async def roms_status(request):
    return web.json_response({"queue": rom_queue})


async def roms_file(request):
    fname = request.match_info["fname"]
    root = pathlib.Path(romdl.STAGING).resolve()
    path = (root / fname).resolve()
    if not path.is_relative_to(root):
        raise web.HTTPForbidden()
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


async def _game_index():
    """Flat launchable-game index from the cabinet's /roms tree, with
    favorite flags merged from state."""
    res = await hub.rpc("library.list", {})
    if not res.get("ok"):
        raise web.HTTPBadGateway(text=res.get("error", "library.list failed"))
    favs = set(load_favorites())
    installed = _installed_roms()
    companion_files = {sys + "/" + f for gid, meta in installed.items()
                       for sys in [gid.split("/", 1)[0]] for f in meta.get("files", [])
                       if sys + "/" + f != gid}
    items = []
    for sys_name, files in sorted(res["result"]["systems"].items()):
        for f in files:
            gid = f"{sys_name}/{f}"
            if gid in companion_files:
                continue
            items.append({"id": gid, "system": sys_name, "file": f,
                          "title": installed.get(gid, {}).get("title", os.path.splitext(f)[0]),
                          "path": f"/roms/{sys_name}/{f}",
                          "favorite": gid in favs})
    return items


def _match_games(items, query, exact):
    if exact:
        return [g for g in items if query in (g["id"], g["file"])]
    q = query.lower()
    hits = [g for g in items if q in g["id"].lower() or q in g.get("title", "").lower()]
    if len(hits) > 1:   # prefer exact-ish hits before declaring ambiguity
        narrowed = [g for g in hits
                    if q in (g["id"].lower(), g["file"].lower(),
                             os.path.splitext(g["file"])[0].lower())]
        if len(narrowed) == 1:
            return narrowed
    return hits


async def games_list(request):
    items = await _game_index()
    if request.query.get("system"):
        items = [g for g in items if g["system"] == request.query["system"]]
    if request.query.get("favorites") in ("1", "true"):
        items = [g for g in items if g["favorite"]]
    return web.json_response({"games": items})


async def games_launch_query(request):
    body = await request.json()
    query, exact = body["query"], bool(body.get("exact"))
    hits = _match_games(await _game_index(), query, exact)
    if not hits:
        return web.json_response({"error": f"no game matches {query!r}"},
                                 status=404)
    if len(hits) > 1:
        return web.json_response(
            {"error": f"ambiguous query {query!r}",
             "candidates": [g["id"] for g in hits]}, status=409)
    g = hits[0]
    res = await hub.rpc("game.launch",
                        {"system": g["system"], "path": g["path"], "args": []})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"game launched by query {query!r}: {g['id']}")
    return web.json_response({"matched": g["id"], **res["result"]})


def load_stream_apps():
    p = pathlib.Path(STATE_DIR) / "library" / "streaming.yaml"
    entries = ((yaml.safe_load(p.read_text()) or {}).get("entries")
               if p.exists() else None)
    return entries or [   # keep in sync with pegasus_gen.gen_streaming defaults
        {"name": "Steam Big Picture", "kind": "moonlight", "target": "Desktop"},
        {"name": "Game Pass Cloud", "kind": "greenlight",
         "target": "Game Pass Cloud"},
    ]


STREAMING_REL = "library/streaming.yaml"
STREAM_DEFAULTS = {"resolution": "1920x1080", "fps": 60, "bitrate_kbps": 20000}


def load_streaming_cfg():
    d = _read_yaml(STREAMING_REL)
    d.setdefault("defaults", dict(STREAM_DEFAULTS))
    d.setdefault("entries", load_stream_apps())
    return d


async def stream_apps(request):
    """Apps with their EFFECTIVE quality (defaults merged with overrides),
    so the UI shows what will actually be used rather than what was typed."""
    cfg = load_streaming_cfg()
    out = []
    for e in cfg["entries"]:
        q = dict(cfg.get("defaults") or {})
        q.update(e.get("quality") or {})
        out.append({**e, "quality": q})
    return web.json_response({"apps": out, "defaults": cfg.get("defaults")})


async def stream_set_quality(request):
    body = await request.json()
    name = body.get("app")
    cfg = load_streaming_cfg()
    fields = {}
    if "resolution" in body:
        r = str(body["resolution"])
        if not re.fullmatch(r"\d{3,4}x\d{3,4}", r):
            return web.json_response(
                {"error": "resolution must look like 1920x1080"}, status=400)
        fields["resolution"] = r
    for k in ("fps", "bitrate_kbps"):
        if k in body:
            try:
                v = int(body[k])
                if v <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return web.json_response(
                    {"error": f"{k} must be a positive integer"}, status=400)
            fields[k] = v
    if not fields:
        return web.json_response({"error": "nothing to change"}, status=400)

    if not name or name == "defaults":
        cfg.setdefault("defaults", {}).update(fields)
        label = "defaults"
    else:
        for e in cfg["entries"]:
            if name.lower() in (str(e.get("name", "")).lower(),
                                str(e.get("target", "")).lower()):
                e.setdefault("quality", {}).update(fields)
                label = e.get("name", name)
                break
        else:
            return web.json_response({"error": f"no stream app {name!r}"},
                                     status=404)
    _write_yaml(STREAMING_REL, cfg)
    git_commit("arcadectl: stream set-quality " + label + " " +
               " ".join(f"{k}={v}" for k, v in fields.items()))
    push_state_soon()
    hub.log_event(f"stream quality {label}: {fields}")
    return web.json_response(cfg)


async def stream_launch(request):
    q = (await request.json())["app"].lower()
    hits = [e for e in load_stream_apps() if q in e["name"].lower()]
    if not hits:
        return web.json_response({"error": f"no stream app matches {q!r}"},
                                 status=404)
    if len(hits) > 1:
        return web.json_response(
            {"error": f"ambiguous app {q!r}",
             "candidates": [e["name"] for e in hits]}, status=409)
    e = hits[0]
    res = await hub.rpc("game.launch", {
        "system": "stream", "path": f"{e['kind']}:{e['target']}", "args": []})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    hub.log_event(f"stream launched: {e['name']}")
    return web.json_response({"matched": e["name"], **res["result"]})


async def cab_logs(request):
    res = await hub.rpc("system.logs", {
        "since": int(request.query.get("since", 0)),
        "lines": int(request.query.get("lines", 200))})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


async def controls_diff(request):
    a, b = request.query.get("a"), request.query.get("b")
    if not a or not b:
        return web.json_response({"error": "need ?a=<preset>&b=<preset>"},
                                 status=400)
    snaps = {}
    for name in (a, b):
        snap = _read_yaml(f"controllers/presets/{name}.yaml")
        if not snap:
            return web.json_response({"error": f"no preset {name}"}, status=404)
        snaps[name] = snap

    def flatten(d, prefix=""):
        out = {}
        for k, v in (d or {}).items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                out.update(flatten(v, key + "."))
            else:
                out[key] = v
        return out

    fa, fb = flatten(snaps[a]), flatten(snaps[b])
    diff = {k: {"a": fa.get(k), "b": fb.get(k)}
            for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)}
    return web.json_response({"a": a, "b": b, "diff": diff})


async def roms_sync(request):
    """Re-drive the delivery pipeline: retry failed queue entries and ask the
    cabinet to rescan its library."""
    retried = []
    for entry in rom_queue:
        if entry["status"] == "ready":
            romdl.remember_source(entry.get("candidate_id", entry["id"]), entry.get("source_metadata"))
            _register_installed_rom(entry)
    async with rom_lock:
        for entry in rom_queue:
            if entry["status"] == "error":
                entry["status"] = "queued"
                entry["detail"] = ""
                asyncio.get_event_loop().create_task(rom_worker(entry))
                retried.append(entry["id"])
    save_rom_queue()
    await hub.rpc("state.sync", {"bundle": build_bundle()}, timeout=45)
    res = await hub.rpc("library.rescan", {})
    hub.log_event(f"roms sync: retried {len(retried)}, rescan ok={res.get('ok')}")
    return web.json_response({"retried": retried,
                              "rescan": bool(res.get("ok"))})


async def state_diff(request):
    rev = request.query.get("rev", "HEAD~1")
    # a "-"-prefixed rev would be parsed by git as a FLAG (e.g.
    # --output=<file> writes into the live state repo) — reject outright
    if not rev or rev.startswith("-"):
        return web.json_response({"error": f"bad rev {rev!r}"}, status=400)
    r = subprocess.run(["git", "-C", STATE_DIR, "diff", rev, "HEAD", "--"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return web.json_response({"error": r.stderr.strip()}, status=400)
    return web.json_response({"rev": rev, "diff": r.stdout})


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


# ------------------------------------------------------------------ box art
#
# The web UI and the Pegasus theme both want cover art. arcaded owns the
# fetch (server/artdb.py) so the cabinet never needs internet egress: art is
# cached here and delivered over the same tunnel as everything else.

def _find_assets():
    """Locate the Y2K asset pack.

    Docker COPYs it to /app/assets next to arcaded.py; a checkout runs the
    daemon from server/ with the pack at the repo root. Try both so `python3
    server/arcaded.py` serves images without needing ASSETS_DIR set.
    """
    if os.environ.get("ASSETS_DIR"):
        return os.environ["ASSETS_DIR"]
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "assets"),
                 os.path.join(os.path.dirname(here), "assets")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(here, "assets")


ASSETS_DIR = _find_assets()


async def art_get(request):
    """Artwork for one game. Also the URL cabd's file.fetch pulls from."""
    kind = request.query.get("kind", "box")
    if kind not in artdb.KINDS:
        return web.json_response(
            {"error": f"kind must be one of {', '.join(artdb.KINDS)}"},
            status=400)
    try:
        body = await artdb.art(request.match_info["system"],
                               request.match_info["rom"], kind)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    if body is None:
        raise web.HTTPNotFound(text="no art for this game")
    # Sniffed, not assumed: Steam's CDN serves JPEG, libretro serves PNG.
    ctype = "image/jpeg" if body[:3] == b"\xff\xd8\xff" else "image/png"
    return web.Response(body=body, content_type=ctype,
                        headers={"Cache-Control": "max-age=86400"})


async def art_status(request):
    return web.json_response(artdb.cache_stats())


async def _art_targets():
    """Every game the cabinet can launch, as (system, rom) art lookups."""
    items = [(g["system"], g["file"]) for g in await _game_index()]
    for t in (_read_yaml("library/steam.yaml").get("titles") or []):
        if t.get("appid"):
            items.append(("steam", str(t["appid"])))
    return items


async def art_warm(request):
    """Pre-fetch art for the whole library into the server-side cache."""
    items = await _art_targets()
    counts = await artdb.warm(items, request.query.get("kind", "box"))
    hub.log_event(f"art warm: {counts['hit']} hit, {counts['miss']} miss")
    return web.json_response({"games": len(items), **counts})


async def art_push(request):
    """Deliver cached art to the cabinet so Pegasus can show boxFront.

    pegasus_gen points every game's `assets.boxFront` at /roms/art/<rom>.png;
    this is what puts the file there. Only already-cached art is pushed —
    call /art/warm first (or let it run, it is idempotent).
    """
    items = await _art_targets()
    await artdb.warm(items)
    pushed, missing = [], 0
    for system, rom in items:
        if artdb.cached_path(system, rom) is None:
            missing += 1
            continue
        stem = artdb.rom_stem(rom)
        res = await hub.rpc("file.fetch", {
            "path": f"/art/{system}/{urllib.parse.quote(rom)}",
            "dest": f"/roms/art/{stem}.png"})
        if res.get("ok"):
            pushed.append(f"{system}/{stem}")
    await hub.rpc("library.rescan", {})
    hub.log_event(f"art pushed to cabinet: {len(pushed)} files, {missing} missing")
    return web.json_response({"pushed": pushed, "missing": missing})


# ------------------------------------------------------- screensaver sources

async def screensaver_remove_source(request):
    """Take a source off the rotation board entirely.

    Distinct from disabling it: the UI's board shows every *added* source
    with an on/off switch, so 'added but off' and 'not added' are different
    states and removal has to actually drop the entry.
    """
    sid = (await request.json())["id"]
    cfg = load_screensaver()
    kept = [s for s in cfg["sources"] if source_id(s) != sid]
    if len(kept) == len(cfg["sources"]):
        return web.json_response({"error": f"no source {sid}"}, status=404)
    cfg["sources"] = kept
    _write_yaml(SCREENSAVER_REL, cfg)
    git_commit(f"arcadectl: screensaver remove-source {sid}")
    push_state_soon()
    hub.log_event(f"screensaver source removed: {sid}")
    return web.json_response(cfg)


# ------------------------------------------------------------------ controls

def _controls_layer(scope):
    """(relpath, whole-file data, bindings dict) for a mapping scope.

    The three layers store their bindings under different keys in different
    files (spec §5.7); every controls route needs the same resolution, so it
    lives here rather than being re-derived per handler.
    """
    if scope == "global":
        rel = "controllers/global.yaml"
        data = _read_yaml(rel)
        return rel, data, data.setdefault("buttons", {})
    if scope.startswith("system:"):
        name = scope.split(":", 1)[1]
        if not name or "/" in name or ".." in name:
            raise ValueError(f"bad system scope {scope!r}")
        rel = f"controllers/systems/{name}.yaml"
        data = _read_yaml(rel)
        return rel, data, data.setdefault("remap", {})
    if scope.startswith("game:"):
        gid = scope.split(":", 1)[1]
        sysname, _, rom = gid.partition("/")
        if (not sysname or not rom or "/" in rom
                or ".." in gid or gid != gid.strip()):
            raise ValueError(f"game scope needs <system>/<rom>, got {gid!r}")
        rel = "controllers/games.yaml"
        data = _read_yaml(rel)
        binds = (data.setdefault("games", {}).setdefault(gid, {})
                 .setdefault("remap", {}))
        return rel, data, binds
    raise ValueError(f"bad scope {scope}")


def _layer_file(scope):
    """(relpath, whole-file data, per-scope dict) — the dict a scope's
    `bindings:` list hangs off. For global/system that is the file root; for
    a game it is that game's entry."""
    if scope == "global":
        rel = "controllers/global.yaml"
        data = _read_yaml(rel)
        return rel, data, data
    if scope.startswith("system:"):
        name = scope.split(":", 1)[1]
        if not name or "/" in name or ".." in name:
            raise ValueError(f"bad system scope {scope!r}")
        rel = f"controllers/systems/{name}.yaml"
        data = _read_yaml(rel)
        return rel, data, data
    if scope.startswith("game:"):
        gid = scope.split(":", 1)[1]
        sysname, _, rom = gid.partition("/")
        if (not sysname or not rom or "/" in rom
                or ".." in gid or gid != gid.strip()):
            raise ValueError(f"game scope needs <system>/<rom>, got {gid!r}")
        rel = "controllers/games.yaml"
        data = _read_yaml(rel)
        return rel, data, data.setdefault("games", {}).setdefault(gid, {})
    raise ValueError(f"bad scope {scope}")


# Mirrors cabinet/inputs.py. Duplicated rather than imported because the
# server does not ship the cabinet package — kept in sync by
# test_binding_validation, which asserts the two lists match.
PAD_TARGETS = ("a", "b", "x", "y", "l", "r", "l2", "r2",
               "start", "select", "up", "down", "left", "right")
ACTION_TARGETS = ("home", "screensaver", "lifeline", "save_state",
                  "load_state", "pause", "reset", "quit",
                  "fast_forward", "rewind", "screenshot")


def _norm_binding(body):
    """Validate a combo/hold binding from the API into its stored form."""
    buttons = body.get("buttons")
    if isinstance(buttons, str):
        buttons = buttons.split("+")
    buttons = [str(b).strip().lower() for b in (buttons or []) if str(b).strip()]
    if not buttons:
        raise ValueError("a binding needs at least one button")
    if len(set(buttons)) != len(buttons):
        raise ValueError("a binding cannot list the same button twice")
    target = str(body.get("target") or "").strip().lower()
    if target not in PAD_TARGETS and target not in ACTION_TARGETS:
        raise ValueError(
            f"unknown target {target!r} — one of: "
            + ", ".join(PAD_TARGETS + ACTION_TARGETS))
    try:
        hold_ms = int(body.get("hold_ms", 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("hold_ms must be an integer") from None
    if hold_ms < 0:
        raise ValueError("hold_ms must not be negative")
    return {"buttons": sorted(buttons), "hold_ms": hold_ms, "target": target}


def _same_expr(a, b):
    return (sorted(a.get("buttons") or []) == sorted(b.get("buttons") or [])
            and int(a.get("hold_ms", 0) or 0) == int(b.get("hold_ms", 0) or 0))


async def controls_bindings(request):
    """Everything bound in one scope: the flat remap AND the combo list."""
    scope = request.query.get("scope", "global")
    try:
        _, _, binds = _controls_layer(scope)
        _, _, holder = _layer_file(scope)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    combos = [c for c in (holder.get("bindings") or []) if isinstance(c, dict)]
    return web.json_response({"scope": scope, "bindings": binds,
                              "combos": combos,
                              "targets": {"pad": list(PAD_TARGETS),
                                          "action": list(ACTION_TARGETS)}})


async def controls_bind(request):
    """Add or replace a combo / hold-to-trigger binding.

    Distinct from /controls/map, which writes the flat physical->logical
    remap RetroArch consumes. A combo cannot be expressed in that file at
    all — cabd detects these itself — so they live in a separate list.
    """
    body = await request.json()
    scope = body.get("scope", "global")
    try:
        rel, data, holder = _layer_file(scope)
        binding = _norm_binding(body)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    combos = holder.setdefault("bindings", [])
    for i, existing in enumerate(list(combos)):
        if isinstance(existing, dict) and _same_expr(existing, binding):
            if existing.get("target") == binding["target"]:
                return web.json_response(
                    {"scope": scope, "binding": binding, "unchanged": True})
            combos[i] = binding          # same expression, new target
            break
    else:
        combos.append(binding)
    _write_yaml(rel, data)
    expr = "+".join(binding["buttons"]) + (
        f" hold {binding['hold_ms']}ms" if binding["hold_ms"] else "")
    git_commit(f"arcadectl: controls bind {scope} {expr} -> {binding['target']}")
    push_state_soon()
    hub.log_event(f"controls bind {scope}: {expr} -> {binding['target']}")
    return web.json_response({"scope": scope, "binding": binding,
                              "combos": combos})


async def controls_unbind_combo(request):
    """Remove a combo binding by its expression (buttons + hold)."""
    body = await request.json()
    scope = body.get("scope", "global")
    try:
        rel, data, holder = _layer_file(scope)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    want = {"buttons": body.get("buttons"), "hold_ms": body.get("hold_ms", 0)}
    if isinstance(want["buttons"], str):
        want["buttons"] = want["buttons"].split("+")
    want["buttons"] = [str(b).strip().lower()
                       for b in (want["buttons"] or []) if str(b).strip()]
    combos = holder.get("bindings") or []
    kept = [c for c in combos
            if not (isinstance(c, dict) and _same_expr(c, want))]
    if len(kept) == len(combos):
        return web.json_response(
            {"error": f"no binding for {'+'.join(want['buttons'])} in {scope}"},
            status=404)
    holder["bindings"] = kept
    _write_yaml(rel, data)
    git_commit(f"arcadectl: controls unbind-combo {scope} "
               + "+".join(want["buttons"]))
    push_state_soon()
    hub.log_event(f"controls unbind-combo {scope}: {want['buttons']}")
    return web.json_response({"scope": scope, "combos": kept})


async def controls_scopes(request):
    """Every scope that currently has bindings, plus 'global'."""
    root = pathlib.Path(STATE_DIR) / "controllers"
    scopes = ["global"]
    sysdir = root / "systems"
    if sysdir.is_dir():
        scopes += [f"system:{p.stem}" for p in sorted(sysdir.glob("*.yaml"))]
    scopes += [f"game:{gid}" for gid in
               sorted((_read_yaml("controllers/games.yaml").get("games") or {}))]
    return web.json_response({"scopes": scopes})


async def controls_unbind(request):
    body = await request.json()
    scope, keys = body["scope"], body.get("keys") or [body["key"]]
    try:
        rel, data, binds = _controls_layer(scope)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)
    removed = [k for k in keys if binds.pop(k, None) is not None]
    if not removed:
        return web.json_response(
            {"error": f"nothing bound to {', '.join(keys)} in {scope}"},
            status=404)
    _write_yaml(rel, data)
    git_commit(f"arcadectl: controls unbind {scope} {' '.join(removed)}")
    push_state_soon()
    hub.log_event(f"controls unbind {scope}: {removed}")
    return web.json_response({"scope": scope, "unbound": removed,
                              "bindings": binds})


async def controls_devices(request):
    """Input devices the cabinet can see — the remapper's device picker."""
    res = await hub.rpc("input.devices", {})
    if not res.get("ok"):
        return web.json_response(res, status=500)
    return web.json_response(res["result"])


# --------------------------------------------------- streaming per-app games

def _find_stream_app(cfg, name):
    want = str(name).lower()
    for e in cfg["entries"]:
        if want in (str(e.get("name", "")).lower(),
                    str(e.get("target", "")).lower()):
            return e
    return None


async def stream_games_set(request):
    """Upsert one game under a streaming app (add, rename target, toggle
    whether it shows on the cabinet, or override its quality)."""
    body = await request.json()
    cfg = load_streaming_cfg()
    app_entry = _find_stream_app(cfg, body.get("app", ""))
    if app_entry is None:
        return web.json_response({"error": f"no stream app {body.get('app')!r}"},
                                 status=404)
    title = str(body.get("title", "")).strip()
    if not title:
        return web.json_response({"error": "title is required"}, status=400)
    games = app_entry.setdefault("games", [])
    game = next((g for g in games if g.get("title") == title), None)
    if game is None:
        if not str(body.get("target", "")).strip():
            return web.json_response(
                {"error": "a new game needs a launch target"}, status=400)
        game = {"title": title, "target": str(body["target"]).strip(),
                "enabled": True}
        games.append(game)
    if "target" in body:
        game["target"] = str(body["target"]).strip()
    if "enabled" in body:
        game["enabled"] = bool(body["enabled"])
    if "quality" in body:
        # null clears the override so the game inherits the app default
        if body["quality"] is None:
            game.pop("quality", None)
        else:
            game["quality"] = body["quality"]
    _write_yaml(STREAMING_REL, cfg)
    git_commit(f"arcadectl: stream game set {app_entry['name']}/{title}")
    push_state_soon()
    hub.log_event(f"stream game set: {app_entry['name']}/{title}")
    return web.json_response(app_entry)


async def stream_games_remove(request):
    body = await request.json()
    cfg = load_streaming_cfg()
    app_entry = _find_stream_app(cfg, body.get("app", ""))
    if app_entry is None:
        return web.json_response({"error": f"no stream app {body.get('app')!r}"},
                                 status=404)
    title = body.get("title")
    games = app_entry.get("games") or []
    kept = [g for g in games if g.get("title") != title]
    if len(kept) == len(games):
        return web.json_response(
            {"error": f"no game {title!r} on {app_entry['name']}"}, status=404)
    app_entry["games"] = kept
    _write_yaml(STREAMING_REL, cfg)
    git_commit(f"arcadectl: stream game remove {app_entry['name']}/{title}")
    push_state_soon()
    hub.log_event(f"stream game removed: {app_entry['name']}/{title}")
    return web.json_response(app_entry)


# ------------------------------------------------------------ rom queue ops

async def roms_retry(request):
    rid = (await request.json())["id"]
    async with rom_lock:
        entry = _queue_entry(rid)
        if entry is None:
            return web.json_response({"error": f"no queue entry {rid}"},
                                     status=404)
        if entry["status"] in ("queued", "searching", "downloading", "delivering"):
            return web.json_response(
                {"error": f"{rid} is already {entry['status']}"}, status=409)
        entry["status"] = "queued"
        entry["detail"] = ""
        entry.pop("bytes_done", None)
        entry.pop("bytes_total", None)
        save_rom_queue()
        asyncio.get_event_loop().create_task(rom_worker(entry))
    hub.log_event(f"ROM retry: {entry['title']}")
    return web.json_response(entry)


async def roms_cancel(request):
    """Cancel a queue entry.

    Queued/terminal rows are dropped immediately. A `downloading` entry is
    cancelled cooperatively: its threading.Event is set, romdl.download
    notices between chunks, removes the partial, and the worker marks the
    row `canceled` (the design's CANCEL button, finally real). `delivering`
    stays uninterruptible — that transfer is the cabinet's to finish and is
    short."""
    rid = (await request.json())["id"]
    async with rom_lock:
        entry = _queue_entry(rid)
        if entry is None:
            return web.json_response({"error": f"no queue entry {rid}"},
                                     status=404)
        if entry["status"] in ("searching", "downloading"):
            ev = rom_cancel_events.get(rid)
            if ev is None:
                return web.json_response(
                    {"error": f"{rid} has no live worker; retry in a moment"},
                    status=409)
            ev.set()
            hub.log_event(f"ROM cancel requested mid-download: {entry['title']}")
            return web.json_response({"cancelled": rid, "was": "downloading"})
        if entry["status"] == "delivering":
            return web.json_response(
                {"error": f"{rid} is delivering to the cabinet; "
                          "let it finish or fail"}, status=409)
        rom_queue.remove(entry)
        save_rom_queue()
    hub.log_event(f"ROM queue entry dropped: {entry['title']}")
    return web.json_response({"cancelled": rid})


# ------------------------------------------------------------------ alerts

# Word -> severity for the presence/event log. The log is free text written
# by every handler, so this classifies rather than parses.
_ALERT_WORDS = (
    ("error", "FAILED"), ("failed", "FAILED"), ("fail", "FAILED"),
    ("disconnected", "WARN"), ("deferred", "WARN"), ("timed out", "WARN"),
    ("offline", "WARN"), ("suppressed", "WARN"),
)


def _severity(text):
    low = text.lower()
    for word, sev in _ALERT_WORDS:
        if word in low:
            return sev
    return "INFO"


async def alerts(request):
    """Severity-tagged recent activity for the dashboard's alerts panel.

    Two sources: the presence/event log (classified by keyword) and the ROM
    queue's own error rows, which carry a real exception repr and would
    otherwise only surface on the ROM tab.
    """
    out = [{"tag": _severity(text), "msg": text, "ts": ts}
           for ts, text in hub.events[-60:]]
    out += [{"tag": "FAILED", "msg": f"{e['title']}: {e['detail']}",
             "ts": None}
            for e in rom_queue if e["status"] == "error"]
    limit = min(int(request.query.get("limit", 20)), 200)
    if request.query.get("severity"):
        want = {s.upper() for s in request.query["severity"].split(",")}
        out = [a for a in out if a["tag"] in want]
    out.sort(key=lambda a: a["ts"] or 0, reverse=True)
    return web.json_response({"alerts": out[:limit]})


# ---------------------------------------------------------------- overview

async def overview(request):
    """Everything the dashboard needs in one round trip.

    The dashboard would otherwise fan out to eight endpoints on every poll,
    and half of them 503 when the cabinet is offline. Here each cabinet-
    dependent section degrades to null independently, so an offline cabinet
    still renders the server-side panels (ROM queue, favorites, state log)
    instead of blanking the page.
    """
    async def maybe(coro):
        try:
            return await coro
        except (web.HTTPException, OSError, ValueError):
            return None

    health = await maybe(hub.rpc("system.health", {}))
    library = await maybe(hub.rpc("library.list", {}))
    games = None
    if library and library.get("ok"):
        favs = set(load_favorites())
        games = [{"id": f"{s}/{f}", "system": s, "file": f,
                  "path": f"/roms/{s}/{f}", "favorite": f"{s}/{f}" in favs}
                 for s, files in sorted(library["result"]["systems"].items())
                 for f in files]
    return web.json_response({
        "online": hub.online,
        "cabinet_state": hub.cab_state,
        "heartbeat_age_s": (round(time.monotonic() - hub.last_hb, 3)
                            if hub.connects else None),
        "health": health.get("result") if health and health.get("ok") else None,
        "games": games,
        "favorites": load_favorites(),
        "audio": load_audio(),
        "screensaver": load_screensaver(),
        "streaming": load_streaming_cfg(),
        "rom_queue": rom_queue,
        "art_cache": artdb.cache_stats(),
    })


# ----------------------------------------------------- outbound integrations
#
# Discord "now playing" and the Home Assistant media_player/device. Both are
# driven off the heartbeat, so nothing polls the cabinet on their behalf.

INTEGRATIONS_REL = integrations.CONFIG_REL


async def _integration_command(kind, payload, leaf):
    """A command arriving from Home Assistant over MQTT."""
    cmd = (payload or "").strip().lower()
    try:
        if kind == "screensaver":
            await hub.rpc("screensaver.on" if cmd in ("on", "true", "1")
                          else "screensaver.off", {})
            hub.log_event(f"home assistant: screensaver {cmd}")
            return
        # media_player commands. "turn_off"/"stop" mean stop the game, not
        # power off the cabinet — HA's off switch should not cut the machine.
        if cmd in ("turn_off", "stop", "media_stop"):
            await hub.rpc("game.kill", {})
        elif cmd in ("pause", "media_pause", "play", "media_play",
                     "media_play_pause"):
            await hub.rpc("input.press", {"key": "pause"})
        else:
            hub.log_event(f"home assistant: ignoring command {cmd!r}")
            return
        hub.log_event(f"home assistant: {cmd}")
    except web.HTTPException as e:
        hub.log_event(f"home assistant command {cmd} failed: {e.reason}")


manager = None      # built at startup so it can capture the running loop


def _snapshot():
    """Current cabinet state in the shape the publishers want, with an art
    URL when the server has a public base configured."""
    cab = hub.cab_state or {}
    cfg = manager.cfg if manager else {}
    art_path = None
    running = cab.get("running")
    if running:
        parts = [p for p in str(running).split("/") if p]
        if len(parts) >= 2:
            art_path = ("/art/" + urllib.parse.quote(parts[-2]) + "/"
                        + urllib.parse.quote(parts[-1]))
    return integrations.snapshot_from(
        cab, hub.online, cfg.get("public_base_url"), art_path)


async def integrations_get(request):
    cfg = manager.cfg if manager else integrations.load_config(STATE_DIR)
    return web.json_response({"config": integrations.redact(cfg),
                              "status": manager.status() if manager else {},
                              "snapshot": _snapshot().as_dict()})


async def integrations_set(request):
    """Patch the integration config. A secret sent back unchanged (the
    redaction sentinel) is left alone, so saving the form does not wipe a
    webhook URL the browser was never given."""
    body = await request.json()
    cfg = integrations.load_config(STATE_DIR)
    for section in ("discord", "homeassistant"):
        patch = body.get(section)
        if not isinstance(patch, dict):
            continue
        for k, v in patch.items():
            if k.endswith("_set"):
                continue
            if isinstance(v, str) and set(v) == {"•"}:
                continue        # unchanged secret placeholder
            cfg.setdefault(section, {})[k] = v
    if "public_base_url" in body:
        cfg["public_base_url"] = body["public_base_url"]
    _write_yaml(INTEGRATIONS_REL, cfg)
    git_commit("arcadectl: integrations set")
    if manager is not None:
        await manager.reload()
    hub.log_event("integrations config updated")
    return web.json_response({"config": integrations.redact(cfg),
                              "status": manager.status() if manager else {}})


async def integrations_test(request):
    which = request.match_info["which"]
    if manager is None:
        return web.json_response({"error": "integrations not started"},
                                 status=503)
    if which == "discord":
        try:
            ok = await manager.discord.test()
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"sent": bool(ok)})
    if which == "homeassistant":
        if not manager.ha.enabled:
            return web.json_response(
                {"error": "home assistant integration is not enabled"},
                status=400)
        ok = await asyncio.to_thread(manager.ha.connect)
        if ok:
            await asyncio.to_thread(manager.ha.publish, _snapshot(), None)
        return web.json_response({"connected": bool(ok),
                                  "note": None if ok else manager.last_error})
    return web.json_response({"error": "discord|homeassistant"}, status=400)


async def _publish_presence():
    """Push current state to the integrations. Called on every heartbeat."""
    if manager is None:
        return
    health = None
    if manager.ha.client is not None:
        # Only the HA sensors need health; do not wake the cabinet for it.
        try:
            res = await asyncio.wait_for(hub.rpc("system.health", {}), 8)
            health = res.get("result") if res.get("ok") else None
        except (web.HTTPException, asyncio.TimeoutError):
            health = None
    await manager.update(_snapshot(), health)


async def _on_startup(app):
    if ROM_QUEUE_PATH.exists():
        rom_queue.extend(json.loads(ROM_QUEUE_PATH.read_text()))
        for entry in rom_queue:
            romdl.remember_source(entry.get("candidate_id", entry["id"]), entry.get("source_metadata"))
            if entry["status"] in ("queued", "searching", "downloading", "delivering"):
                entry["status"] = "queued"
                asyncio.create_task(rom_worker(entry))
    global manager
    manager = integrations.Manager(STATE_DIR, hub.log_event,
                                   _integration_command)
    await manager.start()
    if manager.discord.enabled or manager.ha.enabled:
        hub.log_event(f"integrations started: {manager.status()}")


async def _on_cleanup(app):
    global rom_stopping
    rom_stopping = True
    for event in rom_cancel_events.values():
        event.set()
    if manager is not None:
        await asyncio.to_thread(manager.ha.disconnect)


# Body cap must fit save-backup tarballs (aiohttp's 1 MiB default would
# silently 413 any real /saves tree — savestates alone are MBs each).
app = web.Application(
    client_max_size=int(os.environ.get("ARCADED_MAX_BODY_MB", "512")) * 2**20)
app.add_routes([
    web.get("/", root),
    web.get("/health", health),
    web.get("/status", status),
    web.get("/events", events),
    web.get("/cab", cab_ws),
    web.post("/cab/exec", cab_exec),
    web.get("/cab/screenshot", cab_screenshot),
    web.post("/cab/input", cab_input),
    web.get("/cab/health", cab_health),
    web.post("/cab/clear-cache", cab_clear_cache),
    web.post("/cab/reboot", cab_reboot),
    web.post("/cab/restart-frontend", cab_restart_frontend),
    web.post("/cab/remote-view", cab_remote_view),
    web.get("/cab/vnc-tunnel", vnc_tunnel_ws),
    web.post("/saves/upload", saves_upload),
    web.get("/saves/status", saves_status),
    web.post("/cab/lifeline/{action}", cab_lifeline),
    web.get("/ui", webui),
    web.post("/games/launch", games_launch),
    web.post("/games/launch-query", games_launch_query),
    web.get("/games/list", games_list),
    web.post("/games/kill", games_kill),
    web.get("/stream/apps", stream_apps),
    web.post("/stream/set-quality", stream_set_quality),
    web.post("/stream/launch", stream_launch),
    web.get("/cab/logs", cab_logs),
    web.get("/controls/diff", controls_diff),
    web.post("/roms/sync", roms_sync),
    web.get("/state/diff", state_diff),
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
    web.get("/thumb/{item_id}", thumb_proxy),
    web.post("/screensaver/set-source", screensaver_set_source),
    # NB: must precede the /screensaver/{action} catch-all below — aiohttp
    # resolves in registration order, so a later literal route never wins.
    web.post("/screensaver/remove-source", screensaver_remove_source),
    web.get("/screensaver/playlist", screensaver_playlist),
    web.post("/screensaver/disable", screensaver_disable),
    web.post("/screensaver/{action}", screensaver_cmd),
    web.get("/media/{video_id}", media_proxy),
    web.get("/roms/search", roms_search),
    web.get("/roms/backend", roms_backend),
    web.post("/roms/request", roms_request),
    web.get("/roms/status", roms_status),
    web.get("/roms/files/{fname:.*}", roms_file),
    web.get("/volume", volume_get),
    web.post("/volume/set", volume_set),
    web.get("/favorites", favorites_list),
    web.post("/favorites/add", favorites_add),
    web.post("/favorites/remove", favorites_remove),
    web.get("/overview", overview),
    web.get("/alerts", alerts),
    web.get("/art/status", art_status),
    web.post("/art/warm", art_warm),
    web.post("/art/push", art_push),
    web.get("/art/{system}/{rom}", art_get),
    web.get("/controls/bindings", controls_bindings),
    web.get("/controls/scopes", controls_scopes),
    web.get("/controls/devices", controls_devices),
    web.post("/controls/unbind", controls_unbind),
    web.post("/controls/bind", controls_bind),
    web.post("/controls/unbind-combo", controls_unbind_combo),
    web.post("/stream/games/set", stream_games_set),
    web.post("/stream/games/remove", stream_games_remove),
    web.get("/integrations", integrations_get),
    web.post("/integrations/set", integrations_set),
    web.post("/integrations/test/{which}", integrations_test),
    web.post("/roms/retry", roms_retry),
    web.post("/roms/cancel", roms_cancel),
])
# Y2K asset pack (skins, sprites, tagged GIF library) for the web UI. Static
# rather than a handler: these are ~100 MB of immutable files and aiohttp's
# static route does range requests and conditional GETs for free.
if os.path.isdir(ASSETS_DIR):
    app.add_routes([web.static("/assets", ASSETS_DIR)])
app.on_startup.append(_on_startup)
app.on_cleanup.append(_on_cleanup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                print=None)
