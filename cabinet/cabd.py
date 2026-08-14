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
import socket
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
import metrics  # noqa: E402
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
                    if len(parts) == 2 and parts[1] in ("down", "up"):
                        if combo_engine is not None:
                            combo_engine.key_event(parts[0], parts[1] == "down")
                        if binding_engine is not None:
                            binding_engine.key_event(parts[0], parts[1] == "down")
                        # §5.11: on the lifeline screen, a maps to
                        # "restart networking" — the one on-cabinet repair
                        # action that needs no keyboard and no server.
                        if ll["on"] and parts[0] == "a" and parts[1] == "down":
                            asyncio.create_task(lifeline_restart_network())
                if ss["on"]:
                    asyncio.create_task(screensaver_off())
                # legacy direct combo line (kept for older tests)
                if any(l.startswith("COMBO_HOME") for l in got):
                    asyncio.create_task(game_kill())
        if combo_engine is not None:
            combo_engine.poll()
        if binding_engine is not None:
            binding_engine.poll()
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


LOG_RING = collections.deque(maxlen=1000)   # (seq, ts, line) for `cab logs`
_log_seq = {"n": 0}


def log(*a):
    line = " ".join(str(x) for x in a)
    _log_seq["n"] += 1
    LOG_RING.append((_log_seq["n"],
                     time.strftime("%H:%M:%SZ", time.gmtime()), line))
    print("[cabd]", line, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- games

game = {"session": None, "rom": None, "system": None,
        # elapsed play time: monotonic drives the counter (immune to NTP
        # steps), epoch is what the server shows and hands to Discord/HA
        "started_mono": None, "started_at": None}
# ONE lock for "who owns the screen": games, screensaver, dim clock and
# lifeline all contend for the same display. Separate locks let an in-flight
# screensaver start land on top of a lifeline that had just come up.
game_lock = asyncio.Lock()
ss = {"on": False, "suppress_until": 0.0, "starting": False}
ll = {"on": False, "last_sync": None, "recovery": {}}


def _mark_started(system):
    game["system"] = system
    game["started_mono"] = time.monotonic()
    game["started_at"] = time.time()


def _mark_stopped():
    game["system"] = None
    game["started_mono"] = None
    game["started_at"] = None


def elapsed_s():
    """Seconds the current game has been running, or None."""
    if game["started_mono"] is None:
        return None
    return round(time.monotonic() - game["started_mono"], 1)


async def lifeline_on():
    # The status ladder (ping/DNS) takes seconds — run it BEFORE taking the
    # screen lock, or a lifeline request would stall every game launch for
    # its duration. Nothing else may start a window in the meantime because
    # ll["on"] is set under the lock and every other starter checks it.
    if ll["on"]:
        return {"lifeline": True}
    st = await asyncio.to_thread(
        lifeline.checks, ws_state["connected"], ll["last_sync"])
    # game_lock is the screen-ownership lock: serializes lifeline on/off
    # against each other AND against screensaver/dim-clock/game starts, so
    # nothing can map a window on top of a lifeline that just came up.
    async with game_lock:
        return await _lifeline_on(st)


async def _lifeline_on(st):
    if ll["on"]:
        return {"lifeline": True}
    ll["on"] = True
    if ss["on"]:
        await _screensaver_off()   # we already hold game_lock
    ok = await asyncio.to_thread(lifeline.show, st)
    # §5.11: bring up every recovery path we can reach ourselves. Runs in the
    # background — the diagnostics screen (and this RPC's answer) must never
    # wait on a wedged network command.
    asyncio.create_task(lifeline_recover())
    log(f"lifeline ON (display={ok}); reconnect cap -> 1s; recovery starting")
    return {"lifeline": True}


async def lifeline_recover():
    ll["recovery"] = {"ssh": "starting…", "transport": "starting…"}
    ll["recovery"] = await asyncio.to_thread(lifeline.ensure_recovery_paths)
    log(f"lifeline recovery paths: {ll['recovery']}")


async def lifeline_off():
    async with game_lock:
        ll["on"] = False
        await asyncio.to_thread(lifeline.hide)
        # belt and braces: if a racing show() ever leaked a window, this
        # clears it so the cabinet can never be stuck behind the overlay
        if lifeline.window_present():
            log("lifeline: stray window found after hide; killing")
            await asyncio.to_thread(lifeline.kill_strays)
        log("lifeline OFF")
        return {"lifeline": False}


async def lifeline_restart_network():
    """§5.11 button-press recovery; result shows on the lifeline screen."""
    log("lifeline: restarting networking (panel button)")
    res = await asyncio.to_thread(lifeline.restart_network)
    ll["recovery"] = {**(ll.get("recovery") or {}),
                      "net-restart": "ok" if res["restarted"]
                      else f"failed: {res['detail']}"}
    log(f"lifeline: net restart -> {res}")
    return res


# Auto-surface lifeline after this many seconds without a server link, if no
# game is running. <=0 disables the auto behavior (manual combo still works).
LIFELINE_AUTO_S = float(os.environ.get("LIFELINE_AUTO_S", "60"))


async def lifeline_loop(ws_state):
    """Refresh the status screen every 2s while lifeline is active, and
    auto-raise/-drop it around server-link loss (no keyboard on a cabinet:
    the panel must diagnose itself)."""
    while True:
        await asyncio.sleep(2)
        if LIFELINE_AUTO_S > 0:
            down = ws_state.get("down_since")
            if (not ll["on"] and not ws_state["connected"]
                    and down is not None
                    and time.monotonic() - down >= LIFELINE_AUTO_S
                    and game["session"] is None):
                ll["auto"] = True
                log(f"lifeline: no server link for {LIFELINE_AUTO_S:.0f}s "
                    "and no game — auto-raising")
                await lifeline_on()
            # only auto-drop what was auto-raised: a manually summoned
            # lifeline stays until the combo dismisses it
            elif ll["on"] and ll.get("auto") and ws_state["connected"]:
                ll["auto"] = False
                log("lifeline: server link back — auto-dismissing")
                await lifeline_off()
        if not ll["on"]:
            continue
        try:
            st = await asyncio.to_thread(
                lifeline.checks, ws_state["connected"], ll["last_sync"])
            st["recovery"] = ll.get("recovery") or {}
            # Under the lock, and re-checked inside it: checks() takes
            # seconds (ping/DNS), and an `off` landing in that window would
            # otherwise have show() respawn the overlay right after hide().
            async with game_lock:
                if not ll["on"]:
                    continue
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
            ll["auto"] = False
            await lifeline_off()
        else:
            # summoned by hand: a reconnect must not yank it away mid-read
            ll["auto"] = False
            await lifeline_on()


LOGICAL_COMBOS = ("home", "screensaver", "lifeline")


async def input_press(what):
    """Spec §5.10 synthetic input. Two flavors:
      - a logical combo name (home/screensaver/lifeline): fire the same
        handler the panel combo does — works with no window focused;
      - anything else: an X key spec handed to xdotool (Return, ctrl+q, a).
    """
    if what in LOGICAL_COMBOS:
        await on_combo(what)
        return {"pressed": what, "via": "combo"}
    if ll["on"] and what == "a":
        # §5.10 says synthetic input covers anything scriptable; the panel's
        # lifeline repair button must be one of those things. Without this,
        # `cab press a` went to xdotool and did nothing while the physical
        # button restarted networking.
        await lifeline_restart_network()
        return {"pressed": what, "via": "lifeline"}
    p = await asyncio.to_thread(
        subprocess.run, ["xdotool", "key", "--clearmodifiers", what],
        capture_output=True, text=True, timeout=10)
    if p.returncode != 0:
        raise RuntimeError(f"xdotool key {what!r}: {p.stderr.strip()[:200]}")
    return {"pressed": what, "via": "xdotool"}


async def input_text(text):
    p = await asyncio.to_thread(
        subprocess.run, ["xdotool", "type", "--clearmodifiers", "--", text],
        capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"xdotool type: {p.stderr.strip()[:200]}")
    return {"typed": len(text)}


def _on_evdev_key(button, is_down):
    """Real-hardware key events fan out to both engines: cabinet hotkeys and
    user combo/hold bindings are detected independently and can overlap."""
    if combo_engine is not None:
        combo_engine.key_event(button, is_down)
    if binding_engine is not None:
        binding_engine.key_event(button, is_down)


combo_engine = None   # built in main() once state (hotkeys) is loaded
binding_engine = None  # user combo/hold bindings from controllers/*
virtual_pad = inputs.VirtualPad()


async def on_binding(target, kind):
    """A user combo/hold binding fired."""
    log(f"binding fired: {target} ({kind})")
    if kind == "action":
        await on_action(target)
        return
    # A pad button has to reach the emulator as a real input event, which
    # means uinput. Log the reason when we cannot, so a binding that quietly
    # does nothing on the cabinet is diagnosable from the journal.
    if not virtual_pad.available() and not await asyncio.to_thread(virtual_pad.open):
        log(f"binding {target}: virtual pad unavailable ({virtual_pad.why()})")
        return
    await asyncio.to_thread(virtual_pad.tap, target)


async def on_action(name):
    """Cabinet actions reachable from a binding or synthetic input."""
    if name in LOGICAL_COMBOS:
        await on_combo(name)
        return
    if name == "quit":
        await game_kill()
        return
    cmd = RETRO_COMMANDS.get(name)
    if cmd is None:
        log(f"unknown action {name!r}")
        return
    await retro_command(cmd)


# RetroArch network command interface (cabinet.yaml enables it and pins the
# port); these are the actions it can perform on a running game.
RETRO_COMMANDS = {
    "save_state": "SAVE_STATE", "load_state": "LOAD_STATE",
    "pause": "PAUSE_TOGGLE", "reset": "RESET",
    "fast_forward": "FAST_FORWARD", "rewind": "REWIND",
    "screenshot": "SCREENSHOT",
}


async def retro_command(cmd):
    """Fire-and-forget UDP to RetroArch's network command port."""
    ra = (state["bundle"].get("cabinet.yaml") or {}).get("retroarch") or {}
    port = int(ra.get("network_cmd_port", 55355))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(cmd.encode(), ("127.0.0.1", port))
        s.close()
        return True
    except OSError as e:
        log(f"retroarch command {cmd} failed: {e!r}")
        return False


def collect_bindings(system=None, rom_path=None):
    """Merge the three mapping layers' `bindings:` lists (spec §5.7 order:
    global, then system, then game — later layers append, and a later binding
    on the same expression replaces an earlier one)."""
    bundle = state["bundle"]
    layers = [(bundle.get("controllers/global.yaml") or {}).get("bindings") or []]
    if system:
        layers.append((bundle.get(f"controllers/systems/{system}.yaml")
                       or {}).get("bindings") or [])
        key = renderer.game_key(system, rom_path)
        if key:
            games = (bundle.get("controllers/games.yaml") or {}).get("games") or {}
            layers.append((games.get(key) or {}).get("bindings") or [])
    merged = {}
    for layer in layers:
        for entry in layer:
            if not isinstance(entry, dict):
                continue
            buttons = entry.get("buttons")
            if isinstance(buttons, str):
                buttons = buttons.split("+")
            key = (frozenset(str(b).strip().lower() for b in (buttons or [])),
                   int(entry.get("hold_ms", 0) or 0))
            merged[key] = entry
    return list(merged.values())


def build_combo_engine():
    global combo_engine
    hotkeys = (state["bundle"].get("cabinet.yaml") or {}).get("hotkeys") or {
        "home_combo": "hold:start:2s",
        "screensaver_combo": "hold:select:2s",
        "lifeline_combo": "hold:start+select:5s",
    }
    combo_engine = inputs.ComboEngine(
        hotkeys, lambda name: asyncio.create_task(on_combo(name)))
    build_binding_engine()


def build_binding_engine(system=None, rom_path=None):
    """Rebuilt on state.sync and on every launch — the per-system and
    per-game layers only make sense relative to what is running."""
    global binding_engine
    entries = collect_bindings(system, rom_path)
    binding_engine = inputs.BindingEngine(
        entries,
        lambda target, kind: asyncio.create_task(on_binding(target, kind)))
    if binding_engine.errors:
        log(f"bindings: {len(binding_engine.errors)} bad entr(ies) skipped: "
            + "; ".join(binding_engine.errors[:3]))
    return binding_engine
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
                    if not pl:
                        # Correct-by-design (nothing to cache from a library
                        # the owner disabled) but worth saying out loud: the
                        # offline cache is frozen at its current contents
                        # until a source is re-enabled.
                        log(f"clips sync: no enabled sources; cache frozen at "
                            f"{len(local_clips())} clip(s)")
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
        if ll["on"]:
            # §5.11: lifeline is the ONLY on-cabinet repair surface. Attract
            # video stacking on top of it during an outage is exactly when
            # the operator needs to read it.
            return {"screensaver": False, "note": "lifeline active"}
        # Prime directive 1: a LOCAL game launch must never wait on the
        # server. mpv's window can take the full timeout when the media
        # stream is slow, so cap the in-lock wait at 4s — attract video is
        # never worth stalling a player's launch. If the window shows up
        # later the watchdog's heal path picks it up; if it never does,
        # start() kills it.
        ok = await asyncio.to_thread(screensaver.start, playlist, brightness, 4)
        ss["on"] = bool(ok)
        log(f"screensaver on -> {ss['on']}")
        return {"screensaver": ss["on"]}


async def screensaver_dim_clock(brightness):
    """`fallback: dim_clock`: same lifecycle/flag as media playback."""
    async with game_lock:
        if game["session"] is not None:
            return {"screensaver": False, "note": "game running"}
        if ll["on"]:
            return {"screensaver": False, "note": "lifeline active"}
        ok = await asyncio.to_thread(screensaver.start_dim_clock, brightness)
        ss["on"] = bool(ok)
        log(f"screensaver dim clock -> {ss['on']}")
        return {"screensaver": ss["on"], "mode": "dim_clock"}


async def screensaver_off():
    # Must hold the screen lock: screensaver.start() spends up to its window
    # timeout waiting for mpv, and an unlocked off landing in that window
    # would "stop" a screensaver that then finishes starting and leaves a
    # live window behind — the flag says off, the screen says otherwise.
    async with game_lock:
        return await _screensaver_off()


async def _screensaver_off():
    """Caller must hold game_lock."""
    if not ss["on"]:
        # still sweep: a raced start may have left an untracked window
        if screensaver.window_present():
            await asyncio.to_thread(screensaver.stop, False)
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
        if ss["on"] or ss["starting"] or ll["on"] \
                or game["session"] is not None:
            continue
        if time.monotonic() < ss["suppress_until"]:
            continue
        if time.monotonic() - activity["last"] < idle_s:
            continue
        await start_screensaver_now()


async def start_screensaver_now():
    """Shared by idle timeout and the force combo/CLI: playlist over the
    tunnel, local clip fallback when unreachable (prime directive 6).

    `ss["starting"]` covers the whole decide->flag window for EVERY caller
    (idle, combo, CLI), not just idle: a start takes seconds (playlist
    fetch, then mpv), and a second one begun in that window replaces the
    first process handle, orphaning it on screen.
    """
    if ss["starting"]:
        return {"screensaver": False, "note": "already starting"}
    ss["starting"] = True
    try:
        return await _start_screensaver_now()
    finally:
        ss["starting"] = False


async def _start_screensaver_now():
    cfg = state["bundle"].get("screensaver.yaml") or {}

    async def use_local_clips(why):
        """Same fallback for both no-playlist cases: the server being
        unreachable, and the server answering with nothing to play.
        Honors `fallback:` (spec §5.5): local_clips | dim_clock | off."""
        # re-read: the playlist fetch above can take seconds, and a
        # state.sync landing in that window may have changed the policy we
        # are about to act on
        cfg = state["bundle"].get("screensaver.yaml") or {}
        fallback = cfg.get("fallback", "local_clips")
        clips = local_clips()
        if fallback == "off":
            log(f"idle: {why}; fallback is 'off', staying on the frontend")
            await asyncio.sleep(5)
        elif fallback == "dim_clock":
            log(f"idle: {why}; dim clock fallback")
            await screensaver_dim_clock(cfg.get("brightness", 80))
        elif clips:
            log(f"idle: {why}; local clips fallback")
            await screensaver_on(clips, cfg.get("brightness", 80))
        else:
            log(f"idle: {why}; no fallback available")
            await asyncio.sleep(5)

    try:
        tmo = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=tmo) as s:
            async with s.get(screensaver.MEDIA_BASE
                             + "/screensaver/playlist") as r:
                pl = await r.json()
        if pl.get("playlist"):
            await screensaver_on(pl["playlist"], pl.get("brightness", 80))
        else:
            # A reachable server with zero enabled sources returns an EMPTY
            # playlist. That used to fall through silently: nothing started,
            # nothing logged, and the except-branch fallback never ran
            # because the fetch itself succeeded.
            await use_local_clips("server returned an empty playlist "
                                  "(no enabled sources)")
    except Exception as e:  # noqa: BLE001
        await use_local_clips(f"server unreachable ({e!r})")


MOONLIGHT_BIN = os.environ.get("MOONLIGHT_BIN", "/fakes/moonlight")
GREENLIGHT_BIN = os.environ.get("GREENLIGHT_BIN", "/fakes/greenlight")
DESKTOP_HOST = os.environ.get("STREAM_DESKTOP_HOST", "desktop.lan")


def stream_quality(target):
    """Per-app quality from state, falling back to the defaults block.

    Streams ride the LAN, not the tunnel, so these are deliberately
    independent of the screensaver's 480p cap.
    """
    cfg = state["bundle"].get("library/streaming.yaml") or {}
    q = dict(cfg.get("defaults") or {})
    for e in (cfg.get("entries") or []):
        if e.get("target") == target or e.get("name") == target:
            q.update(e.get("quality") or {})
            break
    return q


def _stream_session(path):
    """path: 'moonlight:<app>' or 'greenlight:<title>' (spec §5.8)."""
    kind, _, target = path.partition(":")
    q = stream_quality(target)
    res = str(q.get("resolution", "1920x1080"))
    fps = str(q.get("fps", 60))
    kbps = int(q.get("bitrate_kbps", 20000))
    if kind == "moonlight":
        # moonlight-qt: --resolution WxH --fps N --bitrate <kbps>
        argv = [MOONLIGHT_BIN, "stream",
                "--resolution", res, "--fps", fps, "--bitrate", str(kbps),
                DESKTOP_HOST, target]
        title = f"^moonlight-fake {target}$"   # anchored: xdotool --name is regex
    elif kind == "greenlight":
        argv = [GREENLIGHT_BIN, "--resolution", res, "--fps", fps,
                "--bitrate", str(kbps), target]
        title = f"^greenlight-fake {target}$"
    else:
        raise ValueError(f"unknown stream kind {kind}")
    log(f"stream {kind}:{target} at {res}@{fps} {kbps}kbps")
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
            _mark_started("stream")
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
            _mark_started("steam")
            log("steam launched:", game["rom"])
            return {"running": game["rom"]}
        # CTL-4: re-render configs from the state cache before EVERY launch
        rendered = await asyncio.to_thread(renderer.render, state["bundle"],
                                           system, path)
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
        _mark_started(system)
        # per-system / per-game binding layers only mean something relative
        # to what is running, so the engine is rebuilt per launch
        build_binding_engine(system, path)
        log("game launched:", game["rom"])
        return {"running": game["rom"]}


async def game_kill():
    async with game_lock:
        if game["session"] is None:
            return {"running": None, "note": "nothing running"}
        await asyncio.to_thread(game["session"].kill)
        game["session"] = None
        game["rom"] = None
        _mark_stopped()
        build_binding_engine()      # back to the global layer only
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
        if ss["on"] and not screensaver.running() and not game_lock.locked():
            # Under the screen lock like every other ss["on"] writer, and
            # skipped entirely while the lock is held: a start in flight is
            # about to set the flag itself, and clearing it here would race
            # that. Non-blocking so the watchdog's 2s game budget is safe.
            async with game_lock:
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


frontend = {"proc": None}


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
        frontend["proc"] = proc
        log("frontend started, pid", proc.pid)
        while proc.poll() is None:
            await asyncio.sleep(0.5)
        log(f"frontend exited rc={proc.returncode}; respawning")
        await asyncio.sleep(0.2)


async def frontend_restart():
    """§6.2 restart-frontend. Two supervision modes: sim/cabd-owned (kill the
    tracked proc, frontend_loop respawns) or systemd-owned (provisioned
    hardware — pegasus.service, cabd's spawn loop disabled)."""
    proc = frontend["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        return {"restarted": "frontend", "mode": "cabd"}
    p = await asyncio.to_thread(
        subprocess.run, ["systemctl", "--user", "restart", "pegasus"],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"systemd restart failed: {p.stderr.strip()[:200]}")
    return {"restarted": "frontend", "mode": "systemd"}


REBOOT_CMD = os.environ.get("CABD_REBOOT_CMD", "systemctl reboot")


def schedule_reboot():
    """§7 system.reboot: ack first, reboot a beat later so the RPC reply
    makes it out. Sim overrides CABD_REBOOT_CMD (can't reboot a container)."""
    subprocess.Popen(["sh", "-c", f"sleep 1; {REBOOT_CMD}"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


sampler = metrics.Sampler()


async def metrics_loop():
    """1 Hz sampling for the health screen's gauges and percentiles.

    Runs in a thread: every reader is a small synchronous sysfs read, and a
    stalled one (a wedged hwmon node) must not block the event loop that also
    carries heartbeats.
    """
    while True:
        try:
            await asyncio.to_thread(sampler.sample)
        except Exception as e:  # noqa: BLE001 — never let telemetry kill cabd
            log(f"metrics sample failed: {e!r}")
        await asyncio.sleep(metrics.INTERVAL_S)


def health_report():
    """Disk / uptime / load / temp / versions — the things that quietly go
    wrong on a cabinet nobody logs into (ROM libraries fill drives)."""
    out = {}
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        out["disk_total_h"] = _human(total)
        out["disk_free_h"] = _human(free)
        out["disk_used_pct"] = round(100.0 * (total - free) / total, 1) if total else 0
    except OSError:
        pass
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        d, rem = divmod(int(secs), 86400)
        h, m = divmod(rem // 60, 60)
        out["uptime_h"] = (f"{d}d {h}h {m}m" if d else f"{h}h {m}m")
    except OSError:
        pass
    try:
        out["load"] = [f"{v:.2f}" for v in os.getloadavg()]
    except OSError:
        pass
    # thermal zone: present on real hardware, usually absent in a container
    try:
        import glob
        for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
            with open(z) as f:
                out["temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
            break
    except (OSError, ValueError):
        pass
    clips = local_clips()
    out["clips"] = len(clips)
    try:
        out["clips_h"] = _human(sum(os.path.getsize(c) for c in clips))
    except OSError:
        out["clips_h"] = "0B"
    vers = []
    for name, argv in (("cabd", None),
                       ("mpv", ["mpv", "--version"]),
                       ("retroarch", [os.environ.get("REAL_RETROARCH",
                                                     "/usr/bin/retroarch"),
                                      "--version"])):
        if argv is None:
            vers.append("cabd/1.0")
            continue
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
            first = (p.stdout or p.stderr).splitlines()
            if first:
                vers.append(" ".join(first[0].split()[:2]))
        except (OSError, subprocess.SubprocessError):
            pass
    out["versions"] = vers
    out["ip"] = lifeline.checks(False, None).get("ip")
    out["ssh_user"] = os.environ.get("CABD_SSH_USER", "arcade")
    out["metrics"] = sampler.summary()
    return out


def clear_caches():
    """Drop the offline clip cache (it refills from the server) and any
    stale render output. Never touches /roms or /saves."""
    freed = 0
    for path in local_clips():
        try:
            freed += os.path.getsize(path)
            os.unlink(path)
        except OSError:
            pass
    for stale in ("/tmp/ss-playlist.m3u", "/tmp/lifeline.png"):
        try:
            freed += os.path.getsize(stale)
            os.unlink(stale)
        except OSError:
            pass
    log(f"caches cleared, freed {_human(freed)}")
    return {"freed_bytes": freed, "freed_h": _human(freed),
            "clips_remaining": len(local_clips())}


NON_GAME_DIRS = {"art", "clips", "favorites", "streaming", "steam"}


def library_list():
    """{system: [rom filenames]} from the /roms tree — emulated systems only
    (streaming/steam/favorites are collections, not launchable rom files)."""
    out = {}
    if os.path.isdir(pegasus_gen.ROMS_DIR):
        for sys_name in sorted(os.listdir(pegasus_gen.ROMS_DIR)):
            d = os.path.join(pegasus_gen.ROMS_DIR, sys_name)
            if not os.path.isdir(d) or sys_name in NON_GAME_DIRS:
                continue
            files = sorted(
                f for f in os.listdir(d)
                if os.path.isfile(os.path.join(d, f))
                and f != "metadata.pegasus.txt" and not f.endswith(".tmp"))
            if files:
                out[sys_name] = files
    return out


# ---- save backup (spec §5: saves live on cabinet disk and are synced up to
# the server opportunistically — a backup, never a dependency; any failure
# is logged and retried next tick).
SAVES_DIR = os.environ.get("CABD_SAVES_DIR", "/saves")
SAVES_SYNC_S = float(os.environ.get("CABD_SAVES_SYNC_SECONDS", "3600"))
saves = {"last_sha": None}


def _saves_tarball():
    """(content-sha256, tar.gz bytes) for SAVES_DIR, or (None, None) when
    there is nothing to back up. The sha covers relative paths + file bytes,
    NOT the tarball (gzip/mtime noise would defeat dedupe)."""
    import hashlib
    import io
    import tarfile
    if not os.path.isdir(SAVES_DIR):
        return None, None
    h = hashlib.sha256()
    n_files = 0
    for root, dirs, names in sorted(os.walk(SAVES_DIR)):
        dirs.sort()
        for name in sorted(names):
            p = os.path.join(root, name)
            # NUL separator + length prefix: without them "x.srm"+"y" and
            # "x.srmy"+"" hash identically (boundary shift), silently
            # deduping away a real change. Filenames cannot contain NUL.
            h.update(os.path.relpath(p, SAVES_DIR).encode() + b"\0")
            with open(p, "rb") as f:
                data = f.read()
            h.update(str(len(data)).encode() + b"\0")
            h.update(data)
            n_files += 1
    if not n_files:
        return None, None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(SAVES_DIR, arcname="saves")
    return h.hexdigest(), buf.getvalue()


async def saves_backup_loop():
    while True:
        try:
            sha, blob = await asyncio.to_thread(_saves_tarball)
            if sha and sha != saves["last_sha"]:
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                            screensaver.MEDIA_BASE + "/saves/upload",
                            data=blob,
                            headers={"X-Saves-Sha256": sha,
                                     "Content-Type": "application/gzip"},
                            timeout=aiohttp.ClientTimeout(total=120)) as r:
                        if r.status == 200:
                            saves["last_sha"] = sha
                            log(f"saves backed up: {len(blob)}B sha {sha[:12]}")
                        else:
                            log(f"saves backup rejected: HTTP {r.status}")
        except Exception as e:  # noqa: BLE001 — opportunistic, never fatal
            log(f"saves backup skipped: {e!r}")
        await asyncio.sleep(SAVES_SYNC_S)


# ---- remote view (spec §5.10): on-demand x11vnc for one-time interactive
# chores (Steam/Xbox logins). x11vnc binds localhost only; every VNC client
# is bridged over a cabinet-dialed WebSocket to the server, so the cabinet
# never needs an inbound port.
rv = {"proc": None}
VNC_LOCAL_PORT = int(os.environ.get("CABD_VNC_PORT", "5900"))


async def remote_view_on():
    if rv["proc"] is None or rv["proc"].poll() is not None:
        rv["proc"] = subprocess.Popen(
            ["x11vnc", "-display", os.environ.get("DISPLAY", ":0"),
             "-rfbport", str(VNC_LOCAL_PORT), "-localhost",
             "-forever", "-shared", "-nopw", "-quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=launcher.die_with_parent())
        for _ in range(40):                      # ack only once RFB accepts
            try:
                _, w = await asyncio.open_connection("127.0.0.1", VNC_LOCAL_PORT)
                w.close()
                break
            except OSError:
                await asyncio.sleep(0.25)
        else:
            rv["proc"].kill()
            rv["proc"] = None
            raise RuntimeError("x11vnc never opened its RFB port")
    log("remote view ON, x11vnc pid", rv["proc"].pid)
    return {"remote_view": True}


async def remote_view_off():
    if rv["proc"] is not None and rv["proc"].poll() is None:
        rv["proc"].terminate()
        try:
            await asyncio.to_thread(rv["proc"].wait, 3)
        except subprocess.TimeoutExpired:
            rv["proc"].kill()
    rv["proc"] = None
    log("remote view OFF")
    return {"remote_view": False}


async def vnc_tunnel(token):
    """Bridge one VNC client: dial the server's tunnel endpoint, pipe bytes
    both ways between that WS and the local x11vnc RFB socket."""
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", VNC_LOCAL_PORT)
    except OSError as e:
        log(f"vnc tunnel {token[:8]}: x11vnc unreachable: {e!r}")
        return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(WS_URL + f"/vnc-tunnel?token={token}",
                                    heartbeat=None) as tws:
                async def ws_to_tcp():
                    async for msg in tws:
                        if msg.type != aiohttp.WSMsgType.BINARY:
                            break
                        writer.write(msg.data)
                        await writer.drain()

                async def tcp_to_ws():
                    while True:
                        data = await reader.read(65536)
                        if not data:
                            break
                        await tws.send_bytes(data)

                done, pending = await asyncio.wait(
                    [asyncio.create_task(ws_to_tcp()),
                     asyncio.create_task(tcp_to_ws())],
                    return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
    except Exception as e:  # noqa: BLE001 — a dead tunnel must not kill cabd
        log(f"vnc tunnel {token[:8]} ended: {e!r}")
    finally:
        writer.close()


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
        elif method == "input.devices":
            result = {"devices": await asyncio.to_thread(inputs.list_devices)}
        elif method == "screensaver.on":
            result = await screensaver_on(params.get("playlist", []),
                                          params.get("brightness", 80))
        elif method == "screensaver.on_fallback":
            # Forced on with no server playlist: same decision the idle path
            # makes — including honoring the `fallback:` config key, which
            # this branch previously ignored (it played clips even when the
            # owner had set fallback: off).
            cfg = state["bundle"].get("screensaver.yaml") or {}
            fallback = cfg.get("fallback", "local_clips")
            clips = local_clips()
            if fallback == "dim_clock":
                result = await screensaver_dim_clock(
                    params.get("brightness", 80))
            elif fallback == "off":
                result = {"screensaver": False, "note": "fallback is 'off'"}
            elif clips:
                result = await screensaver_on(clips,
                                              params.get("brightness", 80))
                log("screensaver forced on from local clips (no sources)")
            else:
                result = {"screensaver": False, "note": "no cached clips"}
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
        elif method == "remote_view.on":
            result = await remote_view_on()
        elif method == "remote_view.off":
            result = await remote_view_off()
        elif method == "vnc.connect":
            asyncio.create_task(vnc_tunnel(params["token"]))
            result = {"connecting": True}
        elif method == "lifeline.restart_network":
            result = await lifeline_restart_network()
        elif method == "input.press":
            result = await input_press(params["key"])
        elif method == "input.text":
            result = await input_text(params["text"])
        elif method == "system.reboot":
            log("reboot requested:", REBOOT_CMD)
            schedule_reboot()
            result = {"rebooting": True}
        elif method == "frontend.restart":
            result = await frontend_restart()
        elif method == "system.health":
            result = await asyncio.to_thread(health_report)
        elif method == "system.clear_cache":
            result = await asyncio.to_thread(clear_caches)
        elif method == "library.list":
            result = {"systems": await asyncio.to_thread(library_list)}
        elif method == "system.logs":
            since = int(params.get("since", 0))
            n = min(int(params.get("lines", 200)), 1000)
            lines = [list(e) for e in LOG_RING if e[0] > since][-n:]
            result = {"lines": lines, "next": _log_seq["n"]}
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
            # assert. Real Pegasus only reads metadata files at startup, so a
            # rescan must re-render them from the current /roms contents AND
            # bounce the frontend — otherwise a rom delivered by the web app
            # is invisible until the next reboot.
            with open("/tmp/pegasus-rescan", "a") as f:
                f.write(f"{time.time()}\n")
            meta = await asyncio.to_thread(pegasus_gen.render_all,
                                           state["bundle"])
            await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "--user", "restart", "pegasus.service"],
                capture_output=True, timeout=30)
            log(f"library rescan: {len(meta)} metadata files; pegasus restarted")
            result = {"rescanned": True, "metadata_files": len(meta)}
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
                                      "system": game["system"],
                                      "started_at": game["started_at"],
                                      "elapsed_s": elapsed_s(),
                                      "screensaver": ss["on"],
                                      "lifeline": ll["on"]}})
        next_t += HB
        delay = next_t - loop.time()
        if delay < 0:
            next_t = loop.time()  # fell behind (stall); resync, don't burst
            delay = 0
        await asyncio.sleep(delay)


# down_since starts at import: "never connected since boot" counts as down,
# so a cabinet that cannot reach the server surfaces lifeline without ever
# having been online.
ws_state = {"connected": False, "down_since": time.monotonic()}


async def session(sess, on_connect):
    async with sess.ws_connect(WS_URL, heartbeat=None) as ws:
        log("connected to", WS_URL)
        ws_state["connected"] = True
        ws_state["down_since"] = None
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
    asyncio.create_task(saves_backup_loop())
    asyncio.create_task(metrics_loop())
    asyncio.create_task(lifeline_loop(ws_state))

    # real hardware: evdev devices feed the same combo engine (A-17)
    evsrc = inputs.EvdevSource(
        on_key=_on_evdev_key,
        on_activity=lambda: activity.__setitem__("last", time.monotonic()))
    # Always run: the source hot-plugs, so a cabinet that boots with zero
    # controllers still picks them up the moment they are connected.
    asyncio.create_task(evsrc.run())
    log(f"evdev source running (hot-plug): {len(evsrc.open_devices())} "
        "device(s) at boot")

    async with aiohttp.ClientSession() as sess:
        while True:  # NET-2: reconnect forever
            try:
                await session(sess, reset_backoff)
                log("connection closed by server")
            except Exception as e:  # noqa: BLE001
                log(f"link error: {e!r}; retry in ~{backoff:.1f}s")
            finally:
                ws_state["connected"] = False
                if ws_state["down_since"] is None:
                    ws_state["down_since"] = time.monotonic()
            # lifeline = maximum reconnect aggression (spec §5.11): 1s cap
            cap = 1.0 if ll["on"] else BACKOFF_CAP
            await asyncio.sleep(min(backoff, cap) * random.uniform(0.8, 1.2))
            backoff = min(backoff * 2, BACKOFF_CAP)


if __name__ == "__main__":
    asyncio.run(main())
