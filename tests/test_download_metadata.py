import importlib.util
from pathlib import Path
import shlex

spec = importlib.util.spec_from_file_location("pegasus_gen", Path(__file__).parents[1] / "cabinet/pegasus_gen.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_disc_tracks_are_hidden_and_space_containing_launch_paths_are_quoted(tmp_path, monkeypatch):
    monkeypatch.setattr(gen, "ROMS_DIR", str(tmp_path))
    monkeypatch.setattr(gen, "ART_DIR", str(tmp_path / "art"))
    monkeypatch.setattr(gen, "CABD_LAUNCH", "/usr/local/bin/cabd-launch")
    (tmp_path / "psx").mkdir()
    cue = "Game Title (USA).cue"
    track = "Game Title (USA) (Track 1).bin"
    (tmp_path / "psx" / cue).touch()
    (tmp_path / "psx" / track).touch()
    bundle = {"cabinet.yaml": {"systems": ["psx"]}, "library/downloads.yaml": {
        "psx/" + cue: {"title": "Game Title", "files": [cue, track]}}}
    gen.gen_system_collections(bundle)
    text = (tmp_path / "psx/metadata.pegasus.txt").read_text()
    assert "game: Game Title\n" in text
    assert f"ignore-file: {tmp_path / 'psx' / track}" in text
    launch = next(l.split(": ", 1)[1] for l in text.splitlines() if l.startswith("launch: "))
    assert shlex.split(launch.replace("{file.path}", str(tmp_path / "psx" / cue))) == [
        "/usr/local/bin/cabd-launch", "psx", str(tmp_path / "psx" / cue)]
