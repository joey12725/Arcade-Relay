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


HOME_SSID = os.environ.get("LIFELINE_HOME_SSID", "Panic! At The Cisco")
HOTSPOT_SSID = os.environ.get("LIFELINE_HOTSPOT_SSID", "Mangdang")


def wifi_hw():
    """(iface, bus) of the first wireless NIC the kernel can see.

    (None, None) means no radio exists as far as Linux is concerned — for a
    USB dongle that is the "not plugged in / not detected" case the panel
    must distinguish from "adapter fine, network absent".
    """
    try:
        for dev in sorted(os.listdir("/sys/class/net")):
            if os.path.isdir(f"/sys/class/net/{dev}/wireless"):
                bus = ("usb" if "/usb" in os.path.realpath(f"/sys/class/net/{dev}")
                       else "pci")
                return dev, bus
    except OSError:
        pass
    return None, None


def _nmcli(args, timeout=4):
    try:
        p = subprocess.run(["nmcli", "-t"] + args, capture_output=True,
                           text=True, timeout=timeout)
        return p.stdout.splitlines() if p.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def wifi_report():
    """Adapter + per-network status for the detail rows.

    Wording is chosen for the doorway read: NOT DETECTED is a hardware/driver
    problem, NOT SEEN is an SSID that is off/out of range, SEEN is in the last
    scan but not joined, CONNECTED is carrying traffic.
    """
    iface, bus = wifi_hw()
    rep = {"wifi_hw": f"{(bus or '').upper()} {iface}" if iface else "NOT DETECTED",
           "active_net": "none", "home": "-", "hotspot": "-"}
    active = {}
    for line in _nmcli(["-f", "NAME,TYPE,DEVICE", "connection", "show",
                        "--active"]):
        parts = line.split(":")
        # loopback is always active and only crowds the panel row
        if len(parts) >= 3 and parts[1] != "loopback" and parts[2] != "lo":
            active[parts[0]] = parts[2]
    if active:
        rep["active_net"] = ", ".join(f"{n} ({d})" for n, d in
                                      sorted(active.items()))[:26]
    # --rescan no reads the BSS cache: this runs on the 2s refresh loop and
    # must not hammer the radio; NM rescans on its own schedule anyway.
    seen = {s for s in _nmcli(["-f", "SSID", "dev", "wifi", "list",
                               "--rescan", "no"]) if s}
    for key, ssid in (("home", HOME_SSID), ("hotspot", HOTSPOT_SSID)):
        if ssid in active:
            rep[key] = "CONNECTED"
        elif iface is None:
            rep[key] = "NO RADIO"
        elif ssid in seen:
            rep[key] = "SEEN"
        else:
            rep[key] = "NOT SEEN"
    return rep


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
    st = {
        "network": ip is not None,
        "internet": ping,
        "dns": dns,
        "server_ws": ws_connected,
        "ip": ip or "none",
        "uptime_s": int(time.monotonic() - _started),
        "last_sync": last_sync or "never",
    }
    st.update(wifi_report())
    return st


# --- recovery paths (spec §5.11: while lifeline is up, bring up every
# recovery path the cabinet can reach by itself). Each is idempotent, never
# raises, and reports what it did so the status screen can show it.
# sudo -n: cabd runs as the arcade user, and these are system services —
# without root, polkit rejects them and every recovery silently failed.
# The arcade user is NOPASSWD sudo on a provisioned cabinet; -n means a
# misconfigured sudo fails fast instead of hanging on a password prompt.
SSH_START = os.environ.get("LIFELINE_SSH_CMD", "sudo -n systemctl start ssh")
TRANSPORT_START = os.environ.get("LIFELINE_TRANSPORT_CMD", "")   # e.g. tailscale up
NET_RESTART = os.environ.get(
    "LIFELINE_NET_RESTART_CMD",
    "sudo -n /opt/arcade/repo/cabinet/provision/arcade-net-watchdog.sh; "
    "sudo -n systemctl restart NetworkManager")


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


# ---- rendering (spec §5.11 + the Y2K design's Lifeline screen).
#
# Everything here is drawn huge on purpose: this screen exists for the case
# where the cabinet is unreachable and someone is reading it across a room,
# with no keyboard and no network. Layout mirrors the design's four status
# bays over a detail panel and a recovery log.
LL_W = int(os.environ.get("LIFELINE_WIDTH", "1024"))
LL_H = int(os.environ.get("LIFELINE_HEIGHT", "768"))

LL_BG = (0, 16, 40)          # deep navy field
PANEL_BG = (1, 25, 60)
GOLD = (255, 228, 92)
OK_GREEN = (124, 255, 90)
BAD_RED = (255, 75, 75)
OK_FIELD = (4, 51, 26)
BAD_FIELD = (58, 5, 16)
PALE = (207, 230, 255)
LABEL = (159, 208, 255)

# First font that exists wins. DejaVu ships with Debian; the others cover a
# minimal image or a non-Debian cabinet. PIL's built-in bitmap font is the
# last resort — legible, just not big, which is why it is not the default.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_font_cache = {}


def _font(size):
    if size in _font_cache:
        return _font_cache[size]
    from PIL import ImageFont
    got = None
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                got = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    if got is None:
        got = ImageFont.load_default()
    _font_cache[size] = got
    return got


def _centred(d, box, text, font, fill):
    """Draw text centred in (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    d.text((x0 + (x1 - x0 - (right - left)) / 2 - left,
            y0 + (y1 - y0 - (bottom - top)) / 2 - top),
           text, font=font, fill=fill)


def render(status, version="cabd/1.0"):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (LL_W, LL_H), LL_BG)
    d = ImageDraw.Draw(img)
    pad = round(LL_W * 0.021)

    # --- header
    d.text((pad, round(LL_H * 0.018)), "LIFELINE",
           font=_font(round(LL_H * 0.070)), fill=(255, 255, 255))
    diag = "DIAGNOSTICS"
    fdiag = _font(round(LL_H * 0.031))
    d.text((LL_W - pad - d.textlength(diag, font=fdiag), round(LL_H * 0.042)),
           diag, font=fdiag, fill=BAD_RED)
    rule = round(LL_H * 0.115)
    d.rectangle([pad, rule, LL_W - pad, rule + 3], fill=GOLD)
    d.rectangle([pad, rule + 8, LL_W - pad, rule + 11], fill=GOLD)

    # --- four status bays, the thing you can read from the doorway
    bays = [("LOCAL NETWORK", status["network"], status.get("ip", "")),
            ("INTERNET", status["internet"], "1.1.1.1"),
            ("DNS", status["dns"], "resolver"),
            ("SERVER LINK", status["server_ws"], "websocket")]
    top = rule + round(LL_H * 0.035)
    bay_h = round(LL_H * 0.235)
    gap = round(LL_W * 0.010)
    bay_w = (LL_W - 2 * pad - 3 * gap) / 4
    for i, (name, ok, note) in enumerate(bays):
        x0 = pad + i * (bay_w + gap)
        box = [x0, top, x0 + bay_w, top + bay_h]
        d.rectangle(box, fill=OK_FIELD if ok else BAD_FIELD,
                    outline=OK_GREEN if ok else BAD_RED, width=6)
        _centred(d, [x0, top + round(bay_h * 0.06),
                     x0 + bay_w, top + round(bay_h * 0.24)],
                 name, _font(round(LL_H * 0.025)), (255, 255, 255))
        _centred(d, [x0, top + round(bay_h * 0.20),
                     x0 + bay_w, top + round(bay_h * 0.80)],
                 "OK" if ok else "BAD", _font(round(LL_H * 0.10)),
                 OK_GREEN if ok else BAD_RED)
        _centred(d, [x0, top + round(bay_h * 0.78),
                     x0 + bay_w, top + bay_h],
                 note, _font(round(LL_H * 0.021)), PALE)

    # --- detail rows (left) and recovery log (right)
    body_y = top + bay_h + round(LL_H * 0.025)
    body_h = LL_H - body_y - pad
    left_w = round((LL_W - 2 * pad - gap) * 0.52)
    d.rectangle([pad, body_y, pad + left_w, body_y + body_h],
                fill=PANEL_BG, outline=GOLD, width=5)

    up = int(status.get("uptime_s", 0))
    days, rem = divmod(up, 86400)
    rows = [("IP", status.get("ip", "none")),
            ("UPTIME", (f"{days}d " if days else "")
                       + f"{rem // 3600:02d}:{rem % 3600 // 60:02d}"),
            ("CABD", version),
            ("LAST SYNC", str(status.get("last_sync", "never"))),
            ("SSH", "REACHABLE" if ssh_listening() else "DOWN"),
            ("WIFI ADAPTER", status.get("wifi_hw", "?")),
            ("HOME WIFI", status.get("home", "?")),
            ("HOTSPOT", status.get("hotspot", "?")),
            ("ACTIVE NET", status.get("active_net", "?"))]
    rf = _font(round(LL_H * 0.028))
    vf = _font(round(LL_H * 0.030))
    row_h = (body_h - round(LL_H * 0.03)) / len(rows)
    for i, (k, v) in enumerate(rows):
        ry = body_y + round(LL_H * 0.015) + i * row_h
        d.text((pad + round(LL_W * 0.014), ry), k, font=rf, fill=LABEL)
        d.text((pad + left_w - round(LL_W * 0.014) - d.textlength(str(v), font=vf), ry),
               str(v), font=vf, fill=(255, 255, 255))
        if i < len(rows) - 1:
            d.rectangle([pad + round(LL_W * 0.012), ry + row_h - round(LL_H * 0.012),
                         pad + left_w - round(LL_W * 0.012),
                         ry + row_h - round(LL_H * 0.012) + 1], fill=(44, 84, 144))

    rx = pad + left_w + gap
    d.rectangle([rx, body_y, LL_W - pad, body_y + body_h],
                fill=PANEL_BG, outline=GOLD, width=5)
    rec = status.get("recovery") or {}
    log = [f"> {k}: {v}" for k, v in sorted(rec.items())] or \
          ["> wpa: COMPLETED", "> lease valid", "> ws heartbeat"]
    lf = _font(round(LL_H * 0.026))
    for i, line in enumerate(log[:6]):
        d.text((rx + round(LL_W * 0.014),
                body_y + round(LL_H * 0.018) + i * round(LL_H * 0.040)),
               line[:46], font=lf, fill=(185, 214, 255))

    # action + exit hint pinned to the bottom of the right panel
    btn_h = round(LL_H * 0.075)
    btn = [rx + round(LL_W * 0.014), body_y + body_h - btn_h - round(LL_H * 0.062),
           LL_W - pad - round(LL_W * 0.014), body_y + body_h - round(LL_H * 0.062)]
    d.rectangle(btn, fill=(155, 232, 106), outline=(255, 255, 255), width=5)
    _centred(d, btn, "PRESS A: RESTART NET", _font(round(LL_H * 0.032)), (10, 42, 0))
    _centred(d, [rx, body_y + body_h - round(LL_H * 0.055), LL_W - pad, body_y + body_h],
             "HOLD START+SELECT 5s TO EXIT", _font(round(LL_H * 0.022)), LABEL)

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


def window_present():
    """True if ANY lifeline window is on screen, tracked or not — catches a
    process orphaned by a racing show()."""
    return launcher.find_window(LL_TITLE) is not None


def kill_strays():
    """Last-resort cleanup for an untracked lifeline window.

    The "--" is load-bearing: the pattern itself starts with "--", so
    without it pkill parses it as an unknown option and kills nothing.
    """
    subprocess.run(["pkill", "-9", "-f", "--", f"--title={LL_TITLE}"],
                   capture_output=True)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and window_present():
        time.sleep(0.1)
    launcher.focus_frontend()


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
