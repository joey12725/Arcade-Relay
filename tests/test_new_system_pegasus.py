"""Disc companions must never turn into separate Pegasus game tiles."""
import importlib.util
from pathlib import Path


def test_dreamcast_gdi_collection_hides_delivered_tracks(tmp_path, monkeypatch):
    path = Path(__file__).parents[1] / "cabinet/pegasus_gen.py"
    spec = importlib.util.spec_from_file_location("pegasus_gen_expanded", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROMS_DIR", str(tmp_path))
    monkeypatch.setattr(module, "ART_DIR", str(tmp_path / "art"))
    directory = tmp_path / "dreamcast"
    directory.mkdir()
    (directory / "Game.gdi").write_text("1\n")
    (directory / "track01.bin").write_bytes(b"track")
    bundle = {
        "cabinet.yaml": {"systems": ["dreamcast"], "emulators": {
            "dreamcast": {"core": "/lib/flycast_libretro.so"}}},
        "library/downloads.yaml": {"dreamcast/Game.gdi": {
            "title": "Game", "files": ["Game.gdi", "track01.bin"]}},
    }
    module.gen_system_collections(bundle)
    metadata = (directory / "metadata.pegasus.txt").read_text()
    assert "extensions: gdi,cdi,chd,cue" in metadata
    assert "ignore-file: " + str(directory / "track01.bin") in metadata
    assert "file: " + str(directory / "Game.gdi") in metadata
    assert "game: track01" not in metadata
