#!/usr/bin/env python3
"""Screensaver engine (SCR-1..7, spec §5.5).

mpv fullscreen above the frontend: --panscan=1.0 --keepaspect crops the
16:9 source to fill the panel with zero bars and zero distortion (SCR-2);
brightness is a multiplicative colorchannelmixer tint, live-adjustable
through mpv's JSON IPC without restarting playback (SCR-3); the process
carries audio class `screensaver` => muted by default policy (SCR-7).
"""
import json
import os
import socket
import subprocess
import time

import launcher

SS_SOCK = "/tmp/mpv-ss.sock"
SS_TITLE = "arcade-screensaver"
MEDIA_BASE = os.environ.get("CABD_MEDIA_BASE", "http://toxiproxy:8666")
PLAYLIST_PATH = "/tmp/ss-playlist.m3u"

_proc = None
_req_id = 0


def _vf(brightness):
    f = max(0, min(int(brightness), 100)) / 100.0
    return f"lavfi=[colorchannelmixer=rr={f}:gg={f}:bb={f}]"


def start_dim_clock(brightness=30):
    """`fallback: dim_clock` (spec §5.5): no media to play, so show a dim
    clock rather than a black screen or the frontend. Same window title and
    process handle as media playback, so on/off, the watchdog and the
    brightness IPC all treat it identically."""
    global _proc
    stop(refocus=False)
    f = max(0, min(int(brightness), 100)) / 100.0
    grey = int(224 * f)
    # A generated source (no media file, no network) with the clock drawn
    # on top: works fully offline, which is the whole point of a fallback.
    _proc = subprocess.Popen(
        ["mpv", "--no-config", "--fs", "--really-quiet", f"--title={SS_TITLE}",
         f"--input-ipc-server={SS_SOCK}", "--vo=x11", "--loop=inf",
         # plain %{localtime}: a strftime argument needs escaped colons,
         # which lavfi's own [...] parsing then mangles — not worth the
         # fight for a fallback clock
         "--vf=lavfi=[drawtext=text='%{localtime}':fontsize=48:"
         f"fontcolor=0x{grey:02x}{grey:02x}{grey:02x}:"
         "x=(w-text_w)/2:y=(h-text_h)/2]",
         "av://lavfi:color=c=black:s=640x480:r=5"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=launcher.die_with_parent())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _proc.poll() is not None:
            return False
        if launcher.find_window(SS_TITLE):
            launcher.focus_window(launcher.find_window(SS_TITLE))
            return True
        time.sleep(0.1)
    _proc.kill()
    return False


def ipc(command, timeout=2.0):
    """One mpv JSON-IPC request; filters out event traffic by request_id."""
    global _req_id
    _req_id += 1
    rid = _req_id
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SS_SOCK)
    try:
        s.sendall((json.dumps({"command": command, "request_id": rid})
                   + "\n").encode())
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buf += s.recv(4096)
            for line in buf.split(b"\n"):
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") == rid:
                    return msg
        raise TimeoutError(f"mpv ipc timeout for {command}")
    finally:
        s.close()


def get_prop(name):
    return ipc(["get_property", name]).get("data")


def start(playlist, brightness):
    """playlist: server-relative paths (/media/<id>). Returns True on window."""
    global _proc
    stop(refocus=False)
    with open(PLAYLIST_PATH, "w") as f:
        # server-relative paths ride the tunnel; absolute paths are the
        # local offline clip cache
        f.write("\n".join(
            p if p.startswith("/var/") or p.startswith("/tmp/")
            else MEDIA_BASE + p
            for p in playlist) + "\n")
    env = dict(os.environ)
    env["PULSE_PROP_media.role"] = "screensaver"
    try:
        os.unlink(SS_SOCK)
    except FileNotFoundError:
        pass
    _proc = subprocess.Popen(
        ["mpv", "--no-config", "--fs", "--panscan=1.0", "--keepaspect",
         f"--input-ipc-server={SS_SOCK}", f"--vf={_vf(brightness)}",
         "--loop-playlist=inf", "--really-quiet", f"--title={SS_TITLE}",
         "--ao=pulse,pipewire,null", "--vo=x11",
         f"--playlist={PLAYLIST_PATH}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, preexec_fn=launcher.die_with_parent())
    deadline = time.monotonic() + 15   # network stream spin-up
    win = None
    while time.monotonic() < deadline:
        if _proc.poll() is not None:
            return False
        win = launcher.find_window(SS_TITLE)
        if win:
            break
        time.sleep(0.1)
    if not win:
        _proc.kill()
        return False
    launcher.focus_window(win)
    return True


def set_brightness(brightness):
    ipc(["vf", "set", _vf(brightness)])
    return True


def running():
    return _proc is not None and _proc.poll() is None


def window_present():
    """True if ANY screensaver window is mapped, tracked or not."""
    return launcher.find_window(SS_TITLE) is not None


def kill_strays():
    """Kill screensaver mpv processes we are no longer tracking.

    start() replaces the module-global handle, so a start that raced another
    start leaves the earlier process running with its window mapped — the
    flag then says "off" while the screen still shows attract video. The
    "--" is required: the pattern begins with "--".
    """
    subprocess.run(["pkill", "-9", "-f", "--", f"--title={SS_TITLE}"],
                   capture_output=True)


def stop(refocus=True):
    global _proc
    if _proc is not None and _proc.poll() is None:
        try:
            ipc(["quit"], timeout=0.5)
        except (OSError, TimeoutError):
            pass
        try:
            _proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _proc.kill()
            _proc.wait(timeout=2)
    _proc = None
    # anything still holding the screensaver window is an orphan from a
    # raced start; reality must match the flag we are about to report
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and launcher.find_window(SS_TITLE):
        kill_strays()
        time.sleep(0.1)
    if refocus:
        launcher.focus_frontend()
