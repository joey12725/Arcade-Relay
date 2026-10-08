"""Render the actual mpv filters: endpoints and fades must affect every channel."""
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "cabinet"))
import screensaver


@pytest.mark.parametrize("brightness,expected", [
    (0, (0, 0, 0)), (50, (0, 32, 127)), (100, (0, 64, 255)),
    (150, (127, 159, 255)), (200, (255, 255, 255)),
])
def test_rendered_brightness(tmp_path, brightness, expected):
    if not shutil.which("mpv"):
        pytest.skip("mpv unavailable")
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "source.ppm"
    source.write_bytes(b"P6\n16 16\n255\n" + bytes((0, 64, 255)) * 256)
    subprocess.run([
        "mpv", "--no-config", "--really-quiet", "--ao=null", "--frames=1",
        "--vo=image", f"--vo-image-outdir={tmp_path}",
        f"--vf={screensaver._vf(brightness)}", str(source),
    ], capture_output=True, check=True, timeout=15)
    output = next(tmp_path.glob("*.jpg"), None) or next(tmp_path.glob("*.png"))
    with Image.open(output) as frame:
        actual = frame.convert("RGB").getpixel((8, 8))
    assert all(abs(a - e) <= 2 for a, e in zip(actual, expected)), actual


def test_white_hides_subtitles_and_leaving_white_restores_them(monkeypatch):
    sent = []
    monkeypatch.setattr(screensaver, "_subs", {"on": True, "brightness": 100})
    monkeypatch.setattr(screensaver, "ipc", lambda cmd: sent.append(cmd) or {})
    screensaver.set_brightness(200)
    assert sent[-1] == ["set_property", "sub-visibility", False]
    screensaver.set_brightness(150)
    assert sent[-1] == ["set_property", "sub-visibility", True]
