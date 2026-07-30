#!/usr/bin/env python3
"""Rolling CPU / GPU / memory sampler for the health screen.

`system.health` reports instantaneous facts (disk, uptime, load). The control
plane's gauges also want utilisation and the p99/p95/p90 of the last 30
minutes, and a percentile needs a window to take it over — so cabd keeps one
here, sampled once a second into a ring buffer.

Cabinet-side on purpose: 1800 samples x 8 metrics shipped over the tunnel on
every dashboard poll would be absurd. Percentiles are computed here and only
the summary crosses the wire.

Every reader is best-effort and returns None when its sysfs node is missing,
so this runs unchanged on the real cabinet (amdgpu), in the container sim
(no GPU, no thermal zone) and on a dev box.
"""
import glob
import math
import os
import re
import time
from collections import deque

WINDOW_S = int(os.environ.get("CABD_METRICS_WINDOW", "1800"))   # 30 minutes
INTERVAL_S = float(os.environ.get("CABD_METRICS_INTERVAL", "1"))

PROC_STAT = "/proc/stat"
PROC_MEMINFO = "/proc/meminfo"
PROC_CPUINFO = "/proc/cpuinfo"


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except (OSError, ValueError):
        return None


def _read_int(path):
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


# ------------------------------------------------------------------- CPU

def parse_stat(text):
    """First 'cpu' line of /proc/stat -> (busy_jiffies, total_jiffies).

    Pure so the delta maths is testable without a running kernel.
    """
    for line in (text or "").splitlines():
        if not line.startswith("cpu "):
            continue
        parts = [int(v) for v in line.split()[1:]]
        if len(parts) < 4:
            return None
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)   # idle + iowait
        total = sum(parts)
        return total - idle, total
    return None


def cpu_percent(prev, cur):
    """Utilisation between two parse_stat() snapshots, or None."""
    if not prev or not cur:
        return None
    busy = cur[0] - prev[0]
    total = cur[1] - prev[1]
    if total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * busy / total))


# ---------------------------------------------------------------- memory

def parse_meminfo(text):
    """/proc/meminfo -> bytes dict. MemAvailable is what 'used' should be
    derived from — MemFree alone counts the page cache as used and makes a
    healthy cabinet look like it is about to die."""
    vals = {}
    for line in (text or "").splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if m:
            vals[m.group(1)] = int(m.group(2)) * 1024
    total = vals.get("MemTotal")
    if not total:
        return None
    avail = vals.get("MemAvailable", vals.get("MemFree", 0))
    swap_total = vals.get("SwapTotal", 0)
    swap_free = vals.get("SwapFree", 0)
    return {"mem_total": total, "mem_used": max(0, total - avail),
            "swap_total": swap_total,
            "swap_used": max(0, swap_total - swap_free)}


# ------------------------------------------------------------------- GPU

# sysfs roots, overridable so the Linux-only readers are testable against
# fixture trees on any OS (and so a weird board can be pointed elsewhere)
DRM_GLOB = os.environ.get("CABD_DRM_GLOB", "/sys/class/drm/card*/device")
THERMAL_GLOB = os.environ.get("CABD_THERMAL_GLOB", "/sys/class/thermal/thermal_zone*")
HWMON_GLOB = os.environ.get("CABD_HWMON_GLOB", "/sys/class/hwmon/hwmon*")


def _amdgpu_dir(drm_glob=None):
    """First DRM card exposing amdgpu's utilisation node."""
    for path in sorted(glob.glob(drm_glob or DRM_GLOB)):
        if os.path.exists(os.path.join(path, "gpu_busy_percent")):
            return path
    return None


def gpu_sample(device_dir=None):
    d = device_dir if device_dir is not None else _amdgpu_dir()
    if not d:
        return {"gpu_pct": None, "vram_used": None, "vram_total": None}
    return {
        "gpu_pct": _read_int(os.path.join(d, "gpu_busy_percent")),
        "vram_used": _read_int(os.path.join(d, "mem_info_vram_used")),
        "vram_total": _read_int(os.path.join(d, "mem_info_vram_total")),
    }


# ---------------------------------------------------------- temperatures

def _hwmon_temp(device_dir, label_match):
    """amdgpu exposes edge/junction/mem temps via hwmon under the card."""
    if not device_dir:
        return None
    for hw in sorted(glob.glob(os.path.join(device_dir, "hwmon", "hwmon*"))):
        for lbl in sorted(glob.glob(os.path.join(hw, "temp*_label"))):
            name = (_read(lbl) or "").strip().lower()
            if label_match in name:
                raw = _read_int(lbl.replace("_label", "_input"))
                return round(raw / 1000.0, 1) if raw is not None else None
        raw = _read_int(os.path.join(hw, "temp1_input"))
        if raw is not None:
            return round(raw / 1000.0, 1)
    return None


def cpu_temp(thermal_glob=None, hwmon_glob=None):
    """Prefer a thermal zone that names itself a CPU/package sensor; the
    first zone on a board is often the chipset, which reads ~20 °C low and
    makes the health screen quietly wrong."""
    zones = sorted(glob.glob(thermal_glob or THERMAL_GLOB))
    fallback = None
    for z in zones:
        kind = (_read(os.path.join(z, "type")) or "").strip().lower()
        raw = _read_int(os.path.join(z, "temp"))
        if raw is None:
            continue
        val = round(raw / 1000.0, 1)
        if any(k in kind for k in ("x86_pkg", "cpu", "core", "k10temp",
                                   "zenpower", "acpitz")):
            return val
        if fallback is None:
            fallback = val
    if fallback is not None:
        return fallback
    for hw in sorted(glob.glob(hwmon_glob or HWMON_GLOB)):
        name = (_read(os.path.join(hw, "name")) or "").strip().lower()
        if name in ("k10temp", "coretemp", "zenpower"):
            raw = _read_int(os.path.join(hw, "temp1_input"))
            if raw is not None:
                return round(raw / 1000.0, 1)
    return None


# ------------------------------------------------------------ percentiles

def percentile(values, pct):
    """Nearest-rank percentile: rank = ceil(P/100 * N), value = sorted[rank-1].

    No numpy on the cabinet, and across 1800 points the difference from an
    interpolated percentile is not worth the dependency. Note `ceil`, not
    `round` — rounding puts p50 of 1..10 at 6.
    """
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    rank = math.ceil(pct / 100.0 * len(vals))
    return vals[max(0, min(len(vals) - 1, rank - 1))]


def static_facts():
    """Things that do not change: CPU model, core count, GPU name."""
    out = {}
    info = _read(PROC_CPUINFO) or ""
    m = re.search(r"^model name\s*:\s*(.+)$", info, re.M)
    if m:
        out["cpu_model"] = m.group(1).strip()
    cores = len(re.findall(r"^processor\s*:", info, re.M))
    if cores:
        out["cpu_threads"] = cores
    ids = set(re.findall(r"^core id\s*:\s*(\d+)$", info, re.M))
    if ids:
        out["cpu_cores"] = len(ids)
    d = _amdgpu_dir()
    if d:
        vendor = (_read(os.path.join(d, "vendor")) or "").strip()
        device = (_read(os.path.join(d, "device")) or "").strip()
        out["gpu_id"] = f"{vendor}:{device}" if vendor and device else "amdgpu"
        total = _read_int(os.path.join(d, "mem_info_vram_total"))
        if total:
            out["vram_total"] = total
    return out


class Sampler:
    """1 Hz ring buffer over the last WINDOW_S seconds."""

    FIELDS = ("cpu_pct", "gpu_pct", "mem_used", "vram_used",
              "cpu_temp_c", "gpu_temp_c")

    def __init__(self, window_s=WINDOW_S, interval_s=INTERVAL_S,
                 now=time.monotonic, drm_glob=None):
        self.window_s = window_s
        self.interval_s = interval_s
        self.now = now
        self.samples = deque(maxlen=int(window_s / max(interval_s, 0.001)) + 8)
        self._prev_stat = None
        self.facts = static_facts()
        self._gpu_dir = _amdgpu_dir(drm_glob)

    def sample(self):
        """Take one reading. Returns the sample dict (also appended)."""
        t = self.now()
        cur = parse_stat(_read(PROC_STAT))
        cpu = cpu_percent(self._prev_stat, cur)
        self._prev_stat = cur

        s = {"t": t, "cpu_pct": cpu, "cpu_temp_c": cpu_temp()}
        s.update(parse_meminfo(_read(PROC_MEMINFO))
                 or {"mem_total": None, "mem_used": None,
                     "swap_total": None, "swap_used": None})
        s.update(gpu_sample(self._gpu_dir))
        s["gpu_temp_c"] = _hwmon_temp(self._gpu_dir, "edge")
        self.samples.append(s)
        self._trim(t)
        return s

    def _trim(self, t):
        cutoff = t - self.window_s
        while self.samples and self.samples[0]["t"] < cutoff:
            self.samples.popleft()

    def latest(self):
        return self.samples[-1] if self.samples else {}

    def summary(self, percentiles=(99, 95, 90)):
        """Current values + percentiles over the window, for system.health."""
        cur = self.latest()
        out = {
            "window_s": self.window_s,
            "samples": len(self.samples),
            "facts": self.facts,
            "current": {k: cur.get(k) for k in
                        ("cpu_pct", "gpu_pct", "mem_used", "mem_total",
                         "vram_used", "vram_total", "swap_used", "swap_total",
                         "cpu_temp_c", "gpu_temp_c")},
            "percentiles": {},
        }
        for field in self.FIELDS:
            vals = [s.get(field) for s in self.samples]
            if not any(v is not None for v in vals):
                continue
            out["percentiles"][field] = {
                f"p{p}": percentile(vals, p) for p in percentiles}
        return out
