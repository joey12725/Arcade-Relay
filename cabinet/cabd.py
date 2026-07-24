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
import time

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio  # noqa: E402
import inputs  # noqa: E402
import pegasus_gen  # noqa: E402
import launcher  # noqa: E402
import lifeline  # noqa: E402
import renderer  # noqa: E402
import screensaver  # noqa: E402

WS_URL = os.environ.get("SERVER_WS_URL", "ws://toxiproxy:8666/cab")
HB = float(os.environ.get("HEARTBEAT_SECONDS", "5"))
BACKOFF_CAP = float(os.environ.get("CABD_BACKOFF_CAP", "10"))
BACKOFF_START = 0.5
FRONTEND_BIN = os.environ.get("FRONTEND_BIN", "/fakes/pegasus")
STATE_CACHE = os.environ.get("STATE_CACHE", "/var/cache/arcade-state.json")
CABD_SOCK = os.environ.get("CABD_SOCK", "/tmp/cabd.sock")
INPUT_FIFO = os.environ.get("CABD_INPUT_FIFO", "/tmp/input-events")
_fifo_fd = None   # persistent nonblocking reader so writers never block


def init_input_fifo():
    """Sim input source (A-13): button events arrive as lines on a FIFO.
    Real hardware reads evdev instead (HARDWARE-TODO)."""
    global _fifo_fd
    try:
        if not os.path.exists(INPUT_FIFO):
            os.mkfifo(INPUT_FIFO)
        _fifo_fd = os.open(INPUT_FIFO, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        log(f"input fifo unavailable: {e}")


import collections

events_buf = collections.deque(maxlen=1000)   # (monotonic_ts, line)
activity = {"last": time.monotonic()}


async def fifo_reader():
    """Continuous input reader: feeds input.monitor, tracks idle activity,
    and exits the screensaver instantly on any input (spec §5.2 #3)."""
    buf = ""
    while True:
        if _fifo_fd is None:
            await asyncio.sleep(1)
            continue
        try:
            chunk = os.read(_fifo_fd, 4096)
        except BlockingIOError:
            chunk = b""
        if chunk:
            buf += chunk.decode(errors="replace")
            *lines, buf = buf.split("\n")
            got = [l for l in lines if l]
            if got:
                now = time.monotonic()
                activity["last"] = now
                for l in got:
                    events_buf.append((now, l))
                    # "<button> down|up" lines feed the PRODUCTION combo
                    # engine (same one the evdev source drives on hardware)
                    parts = l.split()
                    if len(parts) == 2 and parts[1] in ("down", "up") \
                            and combo_engine is not None:
                        combo_engine.key_event(parts[0], parts[1] == "down")
                if ss["on"]:
                    asyncio.create_task(screensaver_off())
                # legacy direct combo line (kept for older tests)
                if any(l.startswith("COMBO_HOME") for l in got):
                    asyncio.create_task(game_kill())
        if combo_engine is not None:
            combo_engine.poll()
        await asyncio.sleep(0.05)


async def monitor_events(seconds):
    start = time.monotonic()
    await asyncio.sleep(seconds)
    return [l for ts, l in events_buf if ts >= start]

# Local state cache (spec §5.2 #7): boots and renders fully offline.
state = {"bundle": {}}


def load_state_cache():
    try:
        with open(STATE_CACHE) as f:
            bundle = json.load(f)
        if not isinstance(bundle, dict):
            return False   # poisoned cache: ignore, wait for a good push
        state["bundle"] = bundle
        return True
    except (OSError, ValueError):
        return False


def save_state_cache(bundle):
    os.makedirs(os.path.dirname(STATE_CACHE), exist_ok=True)
    tmp = STATE_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(bundle, f)
    os.replace(tmp, STATE_CACHE)


def log(*a):
    print("[cabd]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- games

game = {"session": None, "rom": None}
game_lock = asyncio.Lock()
ss = {"on": False, "suppress_until": 0.0}
ll = {"on": False, "last_sync": None}


async def lifeline_on():
    if ll["on"]:
        return {"lifeline": True}
    ll["on"] = True
    if ss["on"]:
        await screensaver_off()
    ok = await asyncio.to_thread(
        lifeline.show, lifeline.checks(ws_state["connected"], ll["last_sync"]))
    log(f"lifeline ON (display={ok}); reconnect cap -> 1s")
    return {"lifeline": True}


async def lifeline_off():
    ll["on"] = False
    await asyncio.to_thread(lifeline.hide)
    log("lifeline OFF")
    return {"lifeline": False}


async def lifeline_loop(ws_state):
    """Refresh the status screen every 2s while lifeline is active."""
    while True:
        await asyncio.sleep(2)
        if not ll["on"]:
            continue
        try:
            st = await asyncio.to_thread(
                lifeline.checks, ws_state["connected"], ll["last_sync"])
            await asyncio.to_thread(lifeline.show, st)
        except Exception as e:  # noqa: BLE001
            log(f"lifeline refresh: {e!r}")


async def on_combo(name):
    log(f"combo fired: {name}")
    if name == "home":
        await game_kill()
    elif name == "screensaver":
        if ss["on"]:
            await screensaver_off()
        else:
            await start_screensaver_now()
    elif name == "lifeline":
        if ll["on"]:
            await lifeline_off()
        else:
            await lifeline_on()


combo_engine = None   # built in main() once state (hotkeys) is loaded


def build_combo_engine():
    global combo_engine
    hotkeys = (state["bundle"].get("cabinet.yaml") or {}).get("hotkeys") or {
        "home_combo": "hold:start:2s",
        "screensaver_combo": "hold:select:2s",
        "lifeline_combo": "hold:start+select:5s",
    }
    combo_engine = inputs.ComboEngine(
        hotkeys, lambda name: asyncio.create_task(on_combo(name)))
CLIPS_DIR = os.environ.get("CABD_CLIPS_DIR", "/var/cache/arcade-clips")


def local_clips():
    try:
        return sorted(os.path.join(CLIPS_DIR, f) for f in os.listdir(CLIPS_DIR)
                      if f.endswith(".ts") and
                      os.path.getsize(os.path.join(CLIPS_DIR, f)) > 100_000)
    except OSError:
        return []


async def clips_sync_loop():
    """Offline fallback cache (SCR §5.5): keep a couple of 480p clips
    pre-pulled over the tunnel so the screensaver works with the link cut."""
    os.makedirs(CLIPS_DIR, exist_ok=True)
    while True:
        try:
            if len(local_clips()) < 2:
                tmo = aiohttp.ClientTimeout(total=None, sock_connect=10,
                                            sock_read=60)
                async with aiohttp.ClientSession(timeout=tmo) as s:
                    async with s.get(screensaver.MEDIA_BASE
                                     + "/screensaver/playlist") as r:
                        pl = (await r.json()).get("playlist", [])
                    for n, path in enumerate(pl[:2]):
                        dst = os.path.join(CLIPS_DIR, f"clip-{n}.ts")
                        if os.path.exists(dst) and os.path.getsize(dst) > 100_000:
                            continue
                        async with s.get(screensaver.MEDIA_BASE + path) as up:
                            with open(dst + ".part", "wb") as f:
                                async for chunk in up.content.iter_chunked(65536):
                                    f.write(chunk)
                        os.replace(dst + ".part", dst)
                        log(f"clip cached: {dst} ({os.path.getsize(dst)} bytes)")
        except Exception as e:  # noqa: BLE001
            log(f"clips sync: {e!r}")
        await asyncio.sleep(15)


async def screensaver_on(playlist, brightness):
    async with game_lock:   # never fight a game launch for the screen
        if game["session"] is not None:
            return {"screensaver": False, "note": "game running"}
        ok = await asyncio.to_thread(screensaver.start, playlist, brightness)
        ss["on"] = bool(ok)
        log(f"screensaver on -> {ss['on']}")
        return {"screensaver": ss["on"]}


async def screensaver_off():
    if not ss["on"]:
        return {"screensaver": False}
    ss["on"] = False
    await asyncio.to_thread(screensaver.stop)
    log("screensaver off")
    return {"screensaver": False}


async def idle_loop():
    """SCR: idle timeout -> screensaver (playlist fetched over the tunnel)."""
    while True:
        await asyncio.sleep(0.5)
        cfg = state["bundle"].get("screensaver.yaml") or {}
        idle_s = float(cfg.get("idle_seconds", 300))
        if ss["on"] or game["session"] is not None:
            continue
        if time.monotonic() < ss["suppress_until"]:
            continue
        if time.monotonic() - activity["last"] < idle_s:
            continue
        await start_screensaver_now()


async def start_screensaver_now():
    """Shared by idle timeout and the force combo/CLI: playlist over the
    tunnel, local clip fallback when unreachable (prime directive 6)."""
    cfg = state["bundle"].get("screensaver.yaml") or {}
    try:
        tmo = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=tmo) as s:
            async with s.get(screensaver.MEDIA_BASE
                             + "/screensaver/playlist") as r:
                pl = await r.json()
        if pl.get("playlist"):
            await screensaver_on(pl["playlist"], pl.get("brightness", 80))
    except Exception as e:  # noqa: BLE001
        clips = local_clips()
        fallback = cfg.get("fallback", "local_clips")
        if clips and fallback == "local_clips":
            log(f"idle: server unreachable ({e!r}); local clips fallback")
            await screensaver_on(clips, cfg.get("brightness", 80))
        else:
            log(f"idle: playlist fetch failed: {e!r}; no fallback available")
            await asyncio.sleep(5)


MOONLIGHT_BIN = os.environ.get("MOONLIGHT_BIN", "/fakes/moonlight")
GREENLIGHT_BIN = os.environ.get("GREENLIGHT_BIN", "/fakes/greenlight")
DESKTOP_HOST = os.environ.get("STREAM_DESKTOP_HOST", "desktop.lan")


def _stream_session(path):
    """path: 'moonlight:<app>' or 'greenlight:<title>' (spec §5.8)."""
    kind, _, target = path.partition(":")
    if kind == "moonlight":
        argv = [MOONLIGHT_BIN, "stream", DESKTOP_HOST, target]
        title = f"^moonlight-fake {target}$"   # anchored: xdotool --name is regex
    elif kind == "greenlight":
        argv = [GREENLIGHT_BIN, target]
        title = f"^greenlight-fake {target}$"
    else:
        raise ValueError(f"unknown stream kind {kind}")
    return launcher.GameSession(path, argv=argv, title=title, probe=False,
                                window_timeout=15)


async def game_launch(system, path, args=()):
    async with game_lock:
        if ss["on"]:
            ss["on"] = False
            await asyncio.to_thread(screensaver.stop, False)
        if game["session"] is not None:
            await asyncio.to_thread(game["session"].kill)
            game["session"] = None
            game["rom"] = None
        if system == "stream":
            sess = await asyncio.to_thread(_stream_session, path)
            game["session"] = sess
            game["rom"] = f"stream/{path}"
            log("stream launched:", game["rom"])
            return {"running": game["rom"]}
        if system == "steam":
            # spec §5.9: steam steam://rungameid/<appid>; window tracked for
            # watchdog/HOME handling like any other session
            argv = [os.environ.get("STEAM_BIN", "/fakes/steam"),
                    f"steam://rungameid/{path}"]
            # Anchored title: xdotool --name is substring regex, so an
            # unanchored "steam-fake 50" would match a stale "steam-fake
            # 504230" window from a previous session.
            sess = await asyncio.to_thread(
                launcher.GameSession, path, (), argv, f"^steam-fake {path}$",
                False, 20)
            game["session"] = sess
            game["rom"] = f"steam/{path}"
            log("steam launched:", game["rom"])
            return {"running": game["rom"]}
        # CTL-4: re-render configs from the state cache before EVERY launch
        rendered = await asyncio.to_thread(renderer.render, state["bundle"], system)
        log("rendered configs:", rendered)
        emulators = (state["bundle"].get("cabinet.yaml") or {}).get("emulators") or {}
        if system in emulators:
            # REAL RetroArch with the rendered config (spec §5.4): if it
            # answers UDP, it consumed network_cmd_enable from that file.
            core = emulators[system]["core"]
            argv = [os.environ.get("REAL_RETROARCH", "/usr/bin/retroarch"),
                    "-L", core, path,
                    "--appendconfig", os.path.join(renderer.RENDER_DIR,
                                                   "retroarch.cfg")]
            sess = await asyncio.to_thread(
                launcher.GameSession, path, (), argv, "RetroArch", True, 20)
        else:
            sess = await asyncio.to_thread(launcher.GameSession, path, tuple(args))
        game["session"] = sess
        game["rom"] = f"{system}/{os.path.basename(path)}"
        log("game launched:", game["rom"])
        return {"running": game["rom"]}


async def game_kill():
    async with game_lock:
        if game["session"] is None:
            return {"running": None, "note": "nothing running"}
        await asyncio.to_thread(game["session"].kill)
        game["session"] = None
        game["rom"] = None
        log("game killed, frontend refocused")
        return {"running": None}


async def _recover_if_current(s, hung):
    """Clear/kill s only if it is still the tracked session (race-safe:
    game_launch may have replaced it between our snapshot and now)."""
    async with game_lock:
        if game["session"] is not s:
            return False
        if hung:
            await asyncio.to_thread(s.kill)   # kill() refocuses frontend
        else:
            launcher.focus_frontend()
        game["session"] = None
        game["rom"] = None
        return True


async def watchdog_loop():
    """EMU-3: dead or hung game -> frontend back within 2s, state cleared.
    Also detects a crashed screensaver (mpv died) so the flag can't go
    stale and idle_loop can relaunch it."""
    silent_since = None
    while True:
        if ss["on"] and not screensaver.running():
            ss["on"] = False
            launcher.focus_frontend()
            log("watchdog: screensaver died, flag cleared")
        s = game["session"]
        if s is None:
            silent_since = None
        elif not s.alive():
            if await _recover_if_current(s, hung=False):
                log("watchdog: game died, frontend refocused")
            silent_since = None
        elif not s.probe:
            silent_since = None   # streams have no UDP iface: never "hung"
        elif launcher.probe_responsive():
            silent_since = None
        else:
            now = time.monotonic()
            if silent_since is None:
                silent_since = now
            elif now - silent_since >= launcher.HANG_UNRESPONSIVE_S:
                if await _recover_if_current(s, hung=True):
                    log("watchdog: game hung, killed")
                silent_since = None
        await asyncio.sleep(launcher.WATCHDOG_POLL_S)


async def file_fetch(path, dest, sha256=None):
    """ROM/media delivery over the tunnel (spec file.*): stream to .part,
    verify checksum, atomic rename."""
    import hashlib
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmo = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=120)
    h = hashlib.sha256()
    n = 0
    try:
        async with aiohttp.ClientSession(timeout=tmo) as s:
            async with s.get(screensaver.MEDIA_BASE + path) as r:
                if r.status != 200:
                    raise RuntimeError(f"fetch {path}: HTTP {r.status}")
                with open(dest + ".part", "wb") as f:
                    async for chunk in r.content.iter_chunked(65536):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
        if sha256 and h.hexdigest() != sha256:
            raise RuntimeError(f"checksum mismatch for {dest}")
    except BaseException:
        try:
            os.unlink(dest + ".part")   # no stale partials on any failure
        except FileNotFoundError:
            pass
        raise
    os.replace(dest + ".part", dest)
    log(f"file fetched: {dest} ({n} bytes)")
    return {"dest": dest, "bytes": n, "sha256": h.hexdigest()}


async def handle_local(reader, writer):
    """Local control socket (prime directive 1): Pegasus launches games via
    cabd-launch -> this socket, never through the server. One JSON line in,
    one JSON line out."""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        data = json.loads(line)
        method = data.get("method")
        params = data.get("params") or {}
        if method == "game.launch":
            result = await game_launch(params["system"], params["path"],
                                       params.get("args", []))
        elif method == "game.kill":
            result = await game_kill()
        elif method == "game.running":
            result = {"running": game["rom"]}
        else:
            raise ValueError(f"unknown method {method}")
        writer.write((json.dumps({"ok": True, "result": result}) + "\n").encode())
    except Exception as e:  # noqa: BLE001 — report to caller, never die
        writer.write((json.dumps({"ok": False, "error": repr(e)}) + "\n").encode())
    try:
        await writer.drain()
        writer.close()
    except OSError:
        pass


async def local_server():
    try:
        os.unlink(CABD_SOCK)
    except FileNotFoundError:
        pass
    await asyncio.start_unix_server(handle_local, path=CABD_SOCK)
    log("local control socket at", CABD_SOCK)


async def audio_loop():
    """Spec §5.6: react to new PipeWire nodes — classify and clamp them
    before anything audible escapes (pactl subscribe is evented)."""
    await asyncio.to_thread(audio.apply_all,
                            audio.policy_from_bundle(state["bundle"]))
    while True:
        try:
            # stdbuf -oL is load-bearing: pactl block-buffers stdout when
            # piped, so without it events sit unseen until 4KB accumulates
            proc = await asyncio.create_subprocess_exec(
                "stdbuf", "-oL", "pactl", "subscribe",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
                except asyncio.TimeoutError:
                    # periodic reconcile backstop (idempotent, no-op when
                    # compliant) in case an event was ever missed
                    await asyncio.to_thread(
                        audio.apply_all, audio.policy_from_bundle(state["bundle"]))
                    continue
                if not line:
                    break
                text = line.decode(errors="replace")
                # react to 'new' AND 'change' (a client un-muting itself gets
                # re-clamped; apply is idempotent so no feedback loop), skip
                # only 'remove'
                if "sink-input" in text and "'remove'" not in text:
                    await asyncio.to_thread(
                        audio.apply_all, audio.policy_from_bundle(state["bundle"]))
        except Exception as e:  # noqa: BLE001
            log(f"audio_loop error: {e!r}")
        await asyncio.sleep(1)   # pactl died (pipewire restart?) — reattach


async def frontend_loop():
    """Spec §5.2 #5: if the frontend dies, restart it."""
    if not os.access(FRONTEND_BIN, os.X_OK):
        log(f"frontend binary {FRONTEND_BIN} missing; supervision disabled")
        return
    while True:
        proc = subprocess.Popen([FRONTEND_BIN],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                preexec_fn=launcher.die_with_parent())
        log("frontend started, pid", proc.pid)
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        log(f"frontend exited rc={proc.returncode}; respawning")
        await asyncio.sleep(0.2)


async def handle_req(ws, data):
    method = data.get("method")
    params = data.get("params") or {}
    try:
        if method == "system.exec":
            p = subprocess.run(["/bin/sh", "-c", params["cmd"]],
                               capture_output=True, text=True, timeout=30)
            result = {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
        elif method == "game.launch":
            result = await game_launch(params["system"], params["path"],
                                       params.get("args", []))
        elif method == "game.kill":
            result = await game_kill()
        elif method == "game.running":
            result = {"running": game["rom"]}
        elif method == "input.monitor":
            secs = min(float(params.get("seconds", 2)), 30)
            result = {"events": await monitor_events(secs)}
        elif method == "screensaver.on":
            result = await screensaver_on(params.get("playlist", []),
                                          params.get("brightness", 80))
        elif method == "screensaver.off":
            result = await screensaver_off()
        elif method == "screensaver.set":
            if ss["on"]:
                await asyncio.to_thread(screensaver.set_brightness,
                                        params["brightness"])
            result = {"screensaver": ss["on"],
                      "brightness": params["brightness"]}
        elif method == "lifeline.on":
            result = await lifeline_on()
        elif method == "lifeline.off":
            result = await lifeline_off()
        elif method == "screensaver.disable":
            secs = float(params.get("seconds", 0))
            ss["suppress_until"] = time.monotonic() + secs
            if secs > 0 and ss["on"]:
                await screensaver_off()
            result = {"suppressed_for_s": secs}
        elif method == "file.fetch":
            result = await file_fetch(params["path"], params["dest"],
                                      params.get("sha256"))
        elif method == "system.screenshot":
            import base64
            p = subprocess.run(
                ["import", "-window", "root", "png:/tmp/screenshot.png"],
                capture_output=True, timeout=15)
            if p.returncode != 0:
                raise RuntimeError(f"screenshot failed: {p.stderr.decode()[:200]}")
            with open("/tmp/screenshot.png", "rb") as f:
                result = {"png_b64": base64.b64encode(f.read()).decode()}
        elif method == "library.rescan":
            # sim frontend has no library; the marker is what test/accept
            # assert, and real Pegasus gets a real rescan trigger here
            with open("/tmp/pegasus-rescan", "a") as f:
                f.write(f"{time.time()}\n")
            log("library rescan triggered")
            result = {"rescanned": True}
        elif method == "state.sync":
            bundle = params["bundle"]
            if not isinstance(bundle, dict):
                raise ValueError(f"bundle must be a dict, got {type(bundle).__name__}")
            save_state_cache(bundle)
            state["bundle"] = bundle
            ll["last_sync"] = time.strftime("%H:%M:%SZ", time.gmtime())
            build_combo_engine()   # hotkeys may have changed
            meta = await asyncio.to_thread(pegasus_gen.render_all, bundle)
            log(f"pegasus metadata rendered: {len(meta)} files")
            applied = await asyncio.to_thread(
                audio.apply_all, audio.policy_from_bundle(bundle))
            result = {"applied": True, "files": len(bundle),
                      "audio_nodes": applied}
            log(f"state synced: {len(bundle)} files cached; audio -> {applied}")
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
    # Absolute-time scheduling: send time must not stretch the period, or
    # delivered-beat counts drift below expectation over long windows.
    loop = asyncio.get_event_loop()
    next_t = loop.time()
    while True:
        await ws.send_json({"kind": "event", "event": "heartbeat", "interval": HB,
                            "state": {"running": game["rom"],
                                      "screensaver": ss["on"],
                                      "lifeline": ll["on"]}})
        next_t += HB
        delay = next_t - loop.time()
        if delay < 0:
            next_t = loop.time()  # fell behind (stall); resync, don't burst
            delay = 0
        await asyncio.sleep(delay)


ws_state = {"connected": False}


async def session(sess, on_connect):
    async with sess.ws_connect(WS_URL, heartbeat=None) as ws:
        log("connected to", WS_URL)
        ws_state["connected"] = True
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

    # post-crash convergence: a previous cabd life may have left an emulator
    # running with no one tracking it — reality must match our empty state
    launcher.kill_strays()

    if load_state_cache():
        log(f"state cache loaded: {len(state['bundle'])} files (offline-capable)")
        try:
            pegasus_gen.render_all(state["bundle"])   # frontend metadata before it starts
        except OSError as e:
            log(f"pegasus metadata render failed: {e!r}")
    else:
        log("no state cache yet; waiting for first state.sync")
    build_combo_engine()

    init_input_fifo()
    await local_server()
    asyncio.create_task(frontend_loop())
    asyncio.create_task(watchdog_loop())
    asyncio.create_task(audio_loop())
    asyncio.create_task(fifo_reader())
    asyncio.create_task(idle_loop())
    asyncio.create_task(clips_sync_loop())
    asyncio.create_task(lifeline_loop(ws_state))

    # real hardware: evdev devices feed the same combo engine (A-17)
    evsrc = inputs.EvdevSource(
        on_key=lambda b, down: combo_engine and combo_engine.key_event(b, down),
        on_activity=lambda: activity.__setitem__("last", time.monotonic()))
    if evsrc.open_devices():
        asyncio.create_task(evsrc.run())
        log(f"evdev sources: {len(evsrc.devices)} device(s)")
    else:
        log("no evdev devices (sim: FIFO source only)")

    async with aiohttp.ClientSession() as sess:
        while True:  # NET-2: reconnect forever
            try:
                await session(sess, reset_backoff)
                log("connection closed by server")
            except Exception as e:  # noqa: BLE001
                log(f"link error: {e!r}; retry in ~{backoff:.1f}s")
            finally:
                ws_state["connected"] = False
            # lifeline = maximum reconnect aggression (spec §5.11): 1s cap
            cap = 1.0 if ll["on"] else BACKOFF_CAP
            await asyncio.sleep(min(backoff, cap) * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, BACKOFF_CAP)


if __name__ == "__main__":
    asyncio.run(main())
