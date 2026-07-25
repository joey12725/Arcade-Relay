#!/usr/bin/env python3
"""Lifeline mode (NET-3, spec §5.11): dead-simple fullscreen status screen,
no dependency on Pegasus or the network stack it diagnoses.

Renders a status PNG with PIL (huge red/blue indicators) and displays it
fullscreen with mpv (own IPC socket, image reloaded in place on refresh).
"""
import json
import os
import socket
import subprocess
import time

import launcher

LL_SOCK = "/tmp/mpv-lifeline.sock"
LL_TITLE = "arcade-lifeline"
LL_PNG = "/tmp/lifeline.png"
RED, BLUE, BG, FG = (226, 59, 59), (43, 79, 216), (16, 18, 26), (238, 240, 248)

_proc = None
_started = time.monotonic()


def checks(ws_connected, last_sync):
    """The §5.11 ladder: local net -> internet -> DNS -> server WS."""
    ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("10.255.255.255", 1))   # no packets sent; picks our IP
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    ping = subprocess.run(["sh", "-c", "command -v ping >/dev/null && ping -c1 -W1 1.1.1.1 >/dev/null 2>&1"],
                          capture_output=True).returncode == 0
    try:
        socket.getaddrinfo("one.one.one.one", 53)
        dns = True
    except OSError:
        dns = False
    return {
        "network": ip is not None,
        "internet": ping,
        "dns": dns,
        "server_ws": ws_connected,
        "ip": ip or "none",
        "uptime_s": int(time.monotonic() - _started),
        "last_sync": last_sync or "never",
    }


# --- recovery paths (spec §5.11: while lifeline is up, bring up every
# recovery path the cabinet can reach by itself). Each is idempotent, never
# raises, and reports what it did so the status screen can show it.
SSH_START = os.environ.get("LIFELINE_SSH_CMD", "systemctl start ssh")
TRANSPORT_START = os.environ.get("LIFELINE_TRANSPORT_CMD", "")   # e.g. tailscale up
NET_RESTART = os.environ.get("LIFELINE_NET_RESTART_CMD",
                             "systemctl restart NetworkManager")


def _sh(cmd, timeout=8):
    """Run a recovery command with a hard deadline.

    Output goes to a temp file rather than a pipe: a recovery command that
    spawns a surviving daemon (sshd, tailscaled) would keep an inherited
    pipe open, and capture_output would block until the timeout even though
    the command itself already returned. Timeouts stay well under the
    server's RPC budget so lifeline.on always answers.
    """
    import tempfile
    try:
        with tempfile.TemporaryFile("w+") as out:
            p = subprocess.run(["sh", "-c", cmd], stdout=out,
                               stderr=subprocess.STDOUT, timeout=timeout)
            out.seek(0)
            return p.returncode == 0, out.read().strip()[:200]
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except (OSError, subprocess.SubprocessError) as e:
        return False, repr(e)[:200]


PROC_TCP = ("/proc/net/tcp", "/proc/net/tcp6")


def ssh_listening(port=22, proc_files=PROC_TCP):
    """True if anything is listening on the SSH port (any bind address).

    Checks tcp6 as well as tcp: Debian's sshd commonly binds :: only, and
    an IPv4-only probe would report 'not listening' forever and restart it
    on every lifeline entry. proc_files is injectable so the tcp6 parse can
    be tested where the kernel has IPv6 disabled.
    """
    for proc in proc_files:
        try:
            with open(proc) as f:
                for line in f.read().splitlines()[1:]:
                    cols = line.split()
                    if int(cols[1].split(":")[1], 16) == port and cols[3] == "0A":
                        return True
        except OSError:
            continue
    return False


def ensure_recovery_paths():
    """Idempotent: only acts on what is actually down."""
    acted = {}
    if ssh_listening():
        acted["ssh"] = "already listening"
    else:
        ok, detail = _sh(SSH_START)
        # a slow starter (systemctl on a loaded boot) may return or time out
        # before the socket is up — give the listener a moment before
        # calling it failed. Retried on every lifeline entry regardless.
        for _ in range(10):
            if ssh_listening():
                break
            time.sleep(0.3)
        acted["ssh"] = ("started" if ssh_listening()
                        else f"failed: {detail or 'no listener'}")
    if TRANSPORT_START:
        ok, detail = _sh(TRANSPORT_START, timeout=8)
        acted["transport"] = "up" if ok else f"failed: {detail}"
    else:
        acted["transport"] = "not configured"
    return acted


def restart_network():
    """Button-press recovery (§5.11): bounce networking, then re-check."""
    ok, detail = _sh(NET_RESTART, timeout=20)
    return {"restarted": ok, "detail": detail}


def render(status, version="cabd/1.0"):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1024, 768), BG)
    d = ImageDraw.Draw(img)
    d.text((40, 30), "LIFELINE", fill=RED)
    rows = [("LOCAL NETWORK", status["network"]),
            ("INTERNET (1.1.1.1)", status["internet"]),
            ("DNS", status["dns"]),
            ("SERVER LINK", status["server_ws"])]
    y = 120
    for label, ok in rows:
        color = BLUE if ok else RED
        d.rectangle([60, y, 160, y + 70], fill=color)
        d.text((80, y + 25), "OK" if ok else "BAD", fill=FG)
        d.text((190, y + 25), label, fill=FG)
        y += 110
    d.text((60, y + 10), f"ip {status['ip']}   uptime {status['uptime_s']}s   "
                         f"{version}   last-sync {status['last_sync']}", fill=FG)
    d.text((60, y + 50), "reconnect: maximum aggression (1s)", fill=BLUE)
    rec = status.get("recovery") or {}
    if rec:
        d.text((60, y + 90), "recovery: " + "  ".join(
            f"{k}={v}" for k, v in sorted(rec.items())), fill=FG)
    d.text((60, y + 130), "press A: restart networking", fill=BLUE)
    img.save(LL_PNG)


def _ipc(command, timeout=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(LL_SOCK)
    try:
        s.sendall((json.dumps({"command": command}) + "\n").encode())
        s.recv(4096)
    finally:
        s.close()


def show(status, version="cabd/1.0"):
    """Start or refresh the fullscreen status display."""
    global _proc
    render(status, version)
    if _proc is not None and _proc.poll() is None:
        try:
            _ipc(["loadfile", LL_PNG, "replace"])
            return True
        except (OSError, TimeoutError):
            pass
    try:
        os.unlink(LL_SOCK)
    except FileNotFoundError:
        pass
    _proc = subprocess.Popen(
        ["mpv", "--no-config", "--fs", "--vo=x11", "--really-quiet",
         f"--title={LL_TITLE}", "--image-display-duration=inf",
         f"--input-ipc-server={LL_SOCK}", LL_PNG],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=launcher.die_with_parent())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if launcher.find_window(LL_TITLE):
            launcher.focus_window(launcher.find_window(LL_TITLE))
            return True
        time.sleep(0.1)
    return False


def running():
    return _proc is not None and _proc.poll() is None


def hide():
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc = None
    launcher.focus_frontend()
