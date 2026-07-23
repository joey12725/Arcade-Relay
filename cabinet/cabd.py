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
import launcher  # noqa: E402
import renderer  # noqa: E402

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


def read_input_events(seconds):
    """Drain stale bytes, then collect events for `seconds`."""
    if _fifo_fd is None:
        return []
    while True:   # drain backlog
        try:
            if not os.read(_fifo_fd, 65536):
                break
        except BlockingIOError:
            break
    out, buf = [], ""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            chunk = os.read(_fifo_fd, 4096)
        except BlockingIOError:
            chunk = b""
        if chunk:
            buf += chunk.decode(errors="replace")
            *lines, buf = buf.split("\n")
            out.extend(l for l in lines if l)
        time.sleep(0.05)
    if buf.strip():
        out.append(buf.strip())
    return out

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


async def game_launch(system, path, args=()):
    async with game_lock:
        if game["session"] is not None:
            await asyncio.to_thread(game["session"].kill)
            game["session"] = None
            game["rom"] = None
        # CTL-4: re-render configs from the state cache before EVERY launch
        rendered = await asyncio.to_thread(renderer.render, state["bundle"], system)
        log("rendered configs:", rendered)
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
    """EMU-3: dead or hung game -> frontend back within 2s, state cleared."""
    silent_since = None
    while True:
        s = game["session"]
        if s is None:
            silent_since = None
        elif not s.alive():
            if await _recover_if_current(s, hung=False):
                log("watchdog: game died, frontend refocused")
            silent_since = None
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
            events = await asyncio.to_thread(read_input_events, secs)
            result = {"events": events}
        elif method == "state.sync":
            bundle = params["bundle"]
            if not isinstance(bundle, dict):
                raise ValueError(f"bundle must be a dict, got {type(bundle).__name__}")
            save_state_cache(bundle)
            state["bundle"] = bundle
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
                            "state": {"running": game["rom"], "screensaver": False}})
        next_t += HB
        delay = next_t - loop.time()
        if delay < 0:
            next_t = loop.time()  # fell behind (stall); resync, don't burst
            delay = 0
        await asyncio.sleep(delay)


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

    # post-crash convergence: a previous cabd life may have left an emulator
    # running with no one tracking it — reality must match our empty state
    launcher.kill_strays()

    if load_state_cache():
        log(f"state cache loaded: {len(state['bundle'])} files (offline-capable)")
    else:
        log("no state cache yet; waiting for first state.sync")

    init_input_fifo()
    await local_server()
    asyncio.create_task(frontend_loop())
    asyncio.create_task(watchdog_loop())
    asyncio.create_task(audio_loop())

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
