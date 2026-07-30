"""Metrics sampler: parsing, percentile maths, and the sysfs readers.

The readers only find real data on the cabinet, but their roots are
injectable, so the amdgpu / thermal logic is exercised here against fixture
trees rather than waiting to be discovered on hardware. Everything degrades
to None when a node is missing, which is what the sim and a dev box get.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cabinet"))
import metrics  # noqa: E402


STAT = """cpu  100 20 30 800 50 0 10 0 0 0
cpu0 50 10 15 400 25 0 5 0 0 0
intr 12345
"""
STAT_LATER = """cpu  150 25 40 900 60 0 12 0 0 0
cpu0 75 12 20 450 30 0 6 0 0 0
intr 12999
"""


def test_parse_stat_splits_busy_and_total():
    busy, total = metrics.parse_stat(STAT)
    assert total == 100 + 20 + 30 + 800 + 50 + 0 + 10
    assert busy == total - (800 + 50)      # idle + iowait are not busy


def test_parse_stat_ignores_junk():
    assert metrics.parse_stat("garbage\nnope") is None
    assert metrics.parse_stat("") is None
    assert metrics.parse_stat(None) is None


def test_cpu_percent_from_two_snapshots():
    a = metrics.parse_stat(STAT)
    b = metrics.parse_stat(STAT_LATER)
    pct = metrics.cpu_percent(a, b)
    # busy delta 50+5+10+2 = 67, total delta 67+100+10 = 177
    assert pct == pytest.approx(100.0 * 67 / 177, rel=1e-6)


def test_cpu_percent_needs_both_snapshots():
    a = metrics.parse_stat(STAT)
    assert metrics.cpu_percent(None, a) is None
    assert metrics.cpu_percent(a, None) is None


def test_cpu_percent_handles_no_elapsed_time():
    a = metrics.parse_stat(STAT)
    assert metrics.cpu_percent(a, a) is None


def test_cpu_percent_is_clamped():
    """A counter reset (suspend/resume) must not produce -400%."""
    a = metrics.parse_stat(STAT_LATER)
    b = metrics.parse_stat(STAT)
    pct = metrics.cpu_percent(a, b)
    assert pct is None or 0.0 <= pct <= 100.0


MEMINFO = """MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    8192000 kB
SwapTotal:       4096000 kB
SwapFree:        3096000 kB
"""


def test_meminfo_uses_available_not_free():
    got = metrics.parse_meminfo(MEMINFO)
    assert got["mem_total"] == 16384000 * 1024
    # used is total-available, so the page cache is not counted as used
    assert got["mem_used"] == (16384000 - 8192000) * 1024
    assert got["swap_used"] == (4096000 - 3096000) * 1024


def test_meminfo_without_available_falls_back_to_free():
    got = metrics.parse_meminfo("MemTotal: 1000 kB\nMemFree: 400 kB\n")
    assert got["mem_used"] == 600 * 1024


def test_meminfo_rejects_junk():
    assert metrics.parse_meminfo("nothing here") is None
    assert metrics.parse_meminfo(None) is None


@pytest.mark.parametrize("pct,expected", [
    (100, 10), (90, 9), (50, 5), (10, 1),
])
def test_percentile_nearest_rank(pct, expected):
    assert metrics.percentile(list(range(1, 11)), pct) == expected


def test_percentile_ignores_none_values():
    assert metrics.percentile([1, None, 2, None, 3], 100) == 3


def test_percentile_of_nothing_is_none():
    assert metrics.percentile([], 99) is None
    assert metrics.percentile([None, None], 99) is None


def test_gpu_sample_without_a_card_is_all_none():
    got = metrics.gpu_sample(device_dir="")
    assert got == {"gpu_pct": None, "vram_used": None, "vram_total": None}


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_sampler_evicts_outside_the_window():
    clock = FakeClock()
    s = metrics.Sampler(window_s=10, interval_s=1, now=clock)
    for i in range(20):
        clock.t = float(i)
        s.sample()
    assert s.samples
    span = s.samples[-1]["t"] - s.samples[0]["t"]
    assert span <= 10


def test_summary_shape_is_stable_without_any_hardware():
    """On a box with no /proc/stat deltas yet and no GPU, summary() must
    still return the keys the health screen reads."""
    clock = FakeClock()
    s = metrics.Sampler(window_s=10, interval_s=1, now=clock)
    s.sample()
    clock.t = 1.0
    s.sample()
    out = s.summary()
    assert set(out) == {"window_s", "samples", "facts", "current", "percentiles"}
    assert out["samples"] == 2
    for key in ("cpu_pct", "gpu_pct", "mem_used", "mem_total",
                "cpu_temp_c", "gpu_temp_c"):
        assert key in out["current"]


def test_summary_percentiles_only_for_populated_fields():
    clock = FakeClock()
    s = metrics.Sampler(window_s=10, interval_s=1, now=clock)
    for i in range(5):
        clock.t = float(i)
        s.samples.append({"t": clock.t, "cpu_pct": float(i * 10),
                          "gpu_pct": None})
    out = s.summary()
    assert "cpu_pct" in out["percentiles"]
    assert "gpu_pct" not in out["percentiles"]
    assert out["percentiles"]["cpu_pct"]["p100" if False else "p99"] == 40.0


# ---------------------------------------------------------------- sysfs
# The amdgpu / thermal readers are Linux-only in production, but they are
# just file reads — pointed at a fixture tree they are exercisable anywhere,
# which is the only way this logic gets covered before it reaches hardware.

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_amdgpu_dir_picks_the_card_with_the_busy_node(tmp_path):
    _write(tmp_path / "card0" / "device" / "unrelated", "x")
    _write(tmp_path / "card1" / "device" / "gpu_busy_percent", "37\n")
    got = metrics._amdgpu_dir(str(tmp_path / "card*" / "device"))
    assert got == str(tmp_path / "card1" / "device")


def test_amdgpu_dir_none_when_no_card(tmp_path):
    assert metrics._amdgpu_dir(str(tmp_path / "card*" / "device")) is None


def test_gpu_sample_reads_utilisation_and_vram(tmp_path):
    d = tmp_path / "device"
    _write(d / "gpu_busy_percent", "61\n")
    _write(d / "mem_info_vram_used", "2147483648\n")
    _write(d / "mem_info_vram_total", "4294967296\n")
    got = metrics.gpu_sample(str(d))
    assert got == {"gpu_pct": 61, "vram_used": 2147483648,
                   "vram_total": 4294967296}


def test_gpu_sample_survives_a_partial_card(tmp_path):
    d = tmp_path / "device"
    _write(d / "gpu_busy_percent", "12\n")     # no vram nodes at all
    got = metrics.gpu_sample(str(d))
    assert got["gpu_pct"] == 12
    assert got["vram_used"] is None


def test_gpu_sample_ignores_unreadable_values(tmp_path):
    d = tmp_path / "device"
    _write(d / "gpu_busy_percent", "not a number\n")
    assert metrics.gpu_sample(str(d))["gpu_pct"] is None


def test_hwmon_temp_prefers_the_labelled_sensor(tmp_path):
    hw = tmp_path / "hwmon" / "hwmon3"
    _write(hw / "temp1_label", "junction\n")
    _write(hw / "temp1_input", "95000\n")
    _write(hw / "temp2_label", "edge\n")
    _write(hw / "temp2_input", "64000\n")
    assert metrics._hwmon_temp(str(tmp_path), "edge") == 64.0


def test_hwmon_temp_falls_back_to_temp1(tmp_path):
    hw = tmp_path / "hwmon" / "hwmon0"
    _write(hw / "temp1_input", "58500\n")
    assert metrics._hwmon_temp(str(tmp_path), "edge") == 58.5


def test_cpu_temp_prefers_a_cpu_zone_over_the_first_one(tmp_path):
    """The first thermal zone is often the chipset — taking it blindly
    reports a number that is ~20C low and looks fine when it is not."""
    z0 = tmp_path / "thermal_zone0"
    _write(z0 / "type", "pch_skylake\n")
    _write(z0 / "temp", "40000\n")
    z1 = tmp_path / "thermal_zone1"
    _write(z1 / "type", "x86_pkg_temp\n")
    _write(z1 / "temp", "72000\n")
    got = metrics.cpu_temp(str(tmp_path / "thermal_zone*"), str(tmp_path / "none*"))
    assert got == 72.0


def test_cpu_temp_falls_back_to_first_readable_zone(tmp_path):
    z0 = tmp_path / "thermal_zone0"
    _write(z0 / "type", "mystery\n")
    _write(z0 / "temp", "51000\n")
    got = metrics.cpu_temp(str(tmp_path / "thermal_zone*"), str(tmp_path / "none*"))
    assert got == 51.0


def test_cpu_temp_uses_hwmon_when_no_thermal_zones(tmp_path):
    hw = tmp_path / "hwmon0"
    _write(hw / "name", "k10temp\n")
    _write(hw / "temp1_input", "63000\n")
    got = metrics.cpu_temp(str(tmp_path / "nozone*"), str(tmp_path / "hwmon*"))
    assert got == 63.0


def test_cpu_temp_none_when_the_board_exposes_nothing(tmp_path):
    assert metrics.cpu_temp(str(tmp_path / "a*"), str(tmp_path / "b*")) is None


def test_sampler_picks_up_a_fixture_gpu(tmp_path):
    d = tmp_path / "card0" / "device"
    _write(d / "gpu_busy_percent", "44\n")
    _write(d / "mem_info_vram_total", "4294967296\n")
    _write(d / "mem_info_vram_used", "1073741824\n")
    s = metrics.Sampler(window_s=10, interval_s=1,
                        drm_glob=str(tmp_path / "card*" / "device"))
    s.sample()
    cur = s.summary()["current"]
    assert cur["gpu_pct"] == 44
    assert cur["vram_total"] == 4294967296
