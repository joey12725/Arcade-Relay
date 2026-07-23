#!/usr/bin/env python3
"""Game launcher + watchdog (M1 core: EMU-2, EMU-3, spec §5.2 #4-5).

Owns the lifecycle of a foreground game process:
  - launch: spawn emulator (fake retroarch in sim), wait for its window,
    focus it
  - kill: polite RetroArch UDP QUIT, then SIGTERM, then SIGKILL — and
    refocus the frontend
  - watchdog: poll process + window; on death/disappearance, refocus the
    frontend within the 2s EMU-3 budget

Standalone: `python3 launcher.py selftest` runs the full scenario matrix
inside the cabinet container (requires DISPLAY + /fakes + a frontend
window). cabd imports this module once M0 acceptance is certified.
"""
import os
import shutil
import socket
import subprocess
import sys
import time

RETROARCH_BIN = os.environ.get("RETROARCH_BIN", "/fakes/retroarch")
FRONTEND_TITLE = os.environ.get("FRONTEND_TITLE", "pegasus-fake")
GAME_TITLE_PREFIX = "retroarch-fake"
UDP_PORT = int(os.environ.get("RETROARCH_UDP_PORT", "55355"))
WATCHDOG_POLL_S = 0.25
RECOVERY_BUDGET_S = 2.0
HANG_UNRESPONSIVE_S = 2.0   # UDP silence with live window => hung game


def _xdotool(*args):
    return subprocess.run(["xdotool", *args], capture_output=True, text=True)


def find_window(title):
    r = _xdotool("search", "--name", title)
    ids = r.stdout.split()
    return ids[0] if ids else None


def focus_window(win_id):
    _xdotool("windowraise", win_id)
    _xdotool("windowfocus", "--sync", win_id)


def focused_window():
    r = _xdotool("getwindowfocus")
    return r.stdout.strip() if r.returncode == 0 else None


def focus_frontend():
    win = find_window(FRONTEND_TITLE)
    if win:
        focus_window(win)
    return win


class GameSession:
    """One running game. Not thread-safe; cabd serializes launches."""

    def __init__(self, rom, extra_args=()):
        self.rom = rom
        self.proc = subprocess.Popen(
            [RETROARCH_BIN, rom, *extra_args],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.window = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"emulator exited rc={self.proc.returncode} before window")
            self.window = find_window(GAME_TITLE_PREFIX)
            if self.window:
                break
            time.sleep(0.05)
        if not self.window:
            self.proc.kill()
            raise RuntimeError("emulator window never appeared")
        focus_window(self.window)

    def alive(self):
        return self.proc.poll() is None and find_window(GAME_TITLE_PREFIX) is not None

    def kill(self):
        """QUIT -> SIGTERM -> SIGKILL escalation; returns when process is gone."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(b"QUIT", ("127.0.0.1", UDP_PORT))
            s.close()
        except OSError:
            pass
        for _ in range(10):                      # <=0.5s for polite exit
            if self.proc.poll() is not None:
                break
            time.sleep(0.05)
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        focus_frontend()


def probe_responsive(timeout=0.2):
    """RetroArch-style UDP GET_STATUS liveness probe."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(b"GET_STATUS", ("127.0.0.1", UDP_PORT))
        s.recvfrom(1024)
        s.close()
        return True
    except OSError:
        return False


def watch(session):
    """Blocking watchdog loop.

    Returns (reason, dt): reason 'died' (process/window gone) or 'hung'
    (window alive but UDP silent for HANG_UNRESPONSIVE_S — killed by us).
    dt is detection->frontend-focused time (detection latency itself is
    bounded by WATCHDOG_POLL_S + probe timeout).
    """
    silent_since = None
    while True:
        if not session.alive():
            t0 = time.monotonic()
            focus_frontend()
            return "died", time.monotonic() - t0
        if probe_responsive():
            silent_since = None
        else:
            now = time.monotonic()
            if silent_since is None:
                silent_since = now
            elif now - silent_since >= HANG_UNRESPONSIVE_S:
                t0 = time.monotonic()
                session.kill()   # escalation handles QUIT-deaf processes
                return "hung", time.monotonic() - t0
        time.sleep(WATCHDOG_POLL_S)


# ---------------------------------------------------------------- selftest

def _assert(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)


def selftest():
    _assert(shutil.which("xdotool"), "xdotool missing")
    _assert(os.access(RETROARCH_BIN, os.X_OK), f"{RETROARCH_BIN} not executable")

    fe = subprocess.Popen([os.path.dirname(RETROARCH_BIN) + "/pegasus"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not find_window(FRONTEND_TITLE):
            time.sleep(0.05)
        _assert(find_window(FRONTEND_TITLE), "frontend window never appeared")

        print("== launch: window appears and takes focus ==")
        s = GameSession("/roms/snes/smw.sfc")
        _assert(focused_window() == s.window, "game window not focused after launch")

        print("== clean kill: frontend refocused ==")
        t0 = time.monotonic()
        s.kill()
        dt = time.monotonic() - t0
        _assert(focused_window() == find_window(FRONTEND_TITLE),
                "frontend not focused after clean kill")
        _assert(dt < RECOVERY_BUDGET_S, f"clean kill took {dt:.2f}s (budget 2s)")
        print(f"   kill->frontend in {dt:.2f}s")

        print("== kill -9: watchdog refocuses frontend within 2s ==")
        s = GameSession("/roms/snes/smw.sfc")
        t_kill = time.monotonic()
        s.proc.kill()          # SIGKILL, as chaos would
        reason, _ = watch(s)   # watchdog notices death, refocuses
        wall = time.monotonic() - t_kill
        _assert(reason == "died", f"expected died, got {reason}")
        _assert(focused_window() == find_window(FRONTEND_TITLE),
                "frontend not focused after kill -9")
        _assert(wall < RECOVERY_BUDGET_S, f"kill->focus wall {wall:.2f}s (budget 2s)")
        print(f"   kill->frontend (incl. detection) in {wall:.2f}s")

        print("== self-crash (--crash-after): watchdog recovers ==")
        s = GameSession("/roms/snes/smw.sfc", extra_args=["--crash-after", "0.5"])
        reason, dt = watch(s)
        _assert(reason == "died", f"expected died, got {reason}")
        _assert(focused_window() == find_window(FRONTEND_TITLE),
                "frontend not focused after crash")
        _assert(dt < RECOVERY_BUDGET_S, f"crash recovery took {dt:.2f}s (budget 2s)")
        print(f"   crash: detection->frontend in {dt:.2f}s")

        print("== hang (--hang-after): UDP-silent game is killed + recovered ==")
        s = GameSession("/roms/snes/smw.sfc", extra_args=["--hang-after", "0.3"])
        t0 = time.monotonic()
        reason, dt = watch(s)
        wall = time.monotonic() - t0
        _assert(reason == "hung", f"expected hung, got {reason}")
        _assert(s.proc.poll() is not None, "hung game process still alive")
        _assert(focused_window() == find_window(FRONTEND_TITLE),
                "frontend not focused after hang recovery")
        _assert(wall < HANG_UNRESPONSIVE_S + 3,
                f"hang total {wall:.2f}s (budget {HANG_UNRESPONSIVE_S + 3}s)")
        print(f"   hang detected+killed+refocused in {wall:.2f}s total")

        print("LAUNCHER-SELFTEST PASS")
    finally:
        fe.terminate()
        try:
            fe.wait(timeout=3)
        except subprocess.TimeoutExpired:
            fe.kill()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        print("usage: launcher.py selftest", file=sys.stderr)
        sys.exit(2)
