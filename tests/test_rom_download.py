"""Transport faults must never become installable files."""
import hashlib
import importlib.util
import io
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("adapter", Path(__file__).parents[1] / "server/romdl.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


@pytest.fixture
def source(tmp_path, monkeypatch):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr("Game (USA).nes", b"NES\x1a" + b"rom bytes" * 200)
    data = payload.getvalue()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data[:len(data)//2] if self.path == "/short" else data)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setattr(adapter, "STAGING", str(tmp_path))
    monkeypatch.setattr(adapter, "BACKEND", "real")
    def candidate(path="/good", **kw):
        adapter._save_cache({"game": {"title": "Game (USA).zip", "system": "nes",
                            "url": f"http://127.0.0.1:{http.server_port}{path}", **kw}})
    yield candidate, data
    http.shutdown()


def test_source_truncation_preserves_existing_archive(source, tmp_path):
    candidate, _ = source
    candidate("/short")
    (tmp_path / "game").mkdir()
    existing = tmp_path / "game" / "Game (USA).zip"
    existing.write_bytes(b"existing")
    with pytest.raises((RuntimeError, OSError)):
        adapter.download("game")
    assert existing.read_bytes() == b"existing"
    assert not list(tmp_path.rglob("*.part"))


def test_source_checksum_and_extracted_cartridge(source, tmp_path):
    candidate, data = source
    candidate(sha256=hashlib.sha256(data).hexdigest())
    result = adapter.download("game")
    assert result["filename"] == "Game (USA).nes"
    assert Path(result["rom_path"]).read_bytes().startswith(b"NES\x1a")
    assert result["sha256"] == hashlib.sha256(Path(result["rom_path"]).read_bytes()).hexdigest()
    assert result["files"][0]["sha256"] == result["sha256"]


def test_wrong_source_checksum_never_publishes(source, tmp_path):
    candidate, _ = source
    candidate(sha256="0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        adapter.download("game")
    assert not (tmp_path / "game" / "Game (USA).zip").exists()
    assert not list(tmp_path.rglob("*.part"))


def test_retained_verified_archive_reinstalls_without_source_network(source, tmp_path, monkeypatch):
    candidate, data = source
    candidate()
    cache = adapter._load_cache()
    cache["game"]["url"] = "https://mirror.invalid/rom?id=123"
    cache["game"]["source_metadata"] = {"file_name": "Game (USA).zip", "size": len(data),
                                        "sha256": hashlib.sha256(data).hexdigest()}
    adapter._save_cache(cache)
    (tmp_path / "game").mkdir()
    (tmp_path / "game/Game (USA).zip").write_bytes(data)
    def unexpected_fetch(*args):
        raise AssertionError("verified retained archives must not need the source network")
    monkeypatch.setattr(adapter, "_fetch_torrent", unexpected_fetch)
    result = adapter.download("game")
    assert result["filename"] == "Game (USA).nes"
    assert result["source_metadata"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["source_reused"] is True


def test_search_refresh_preserves_verified_source_metadata(source, monkeypatch):
    _, data = source
    url = "https://mirror.invalid/rom?id=123#Game.zip"
    rid = adapter._candidate_id(url)
    metadata = {"file_name": "Game (USA).zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    adapter._save_cache({rid: {"title": "Game (USA).zip", "source_metadata": metadata}})
    monkeypatch.setattr(adapter, "_run_romdl", lambda *args: [{"title": "Game (USA).zip", "url": url, "score": 135}])
    adapter.search("Game", "nes")
    assert adapter._load_cache()[rid]["source_metadata"] == metadata


@pytest.mark.parametrize("system,launch_name", [
    ("gbc", "Game.gbc"), ("n64", "Game.z64"), ("nds", "Game.nds"),
    ("3ds", "Game.3ds"), ("ps2", "Game.iso"),
    ("psp", "Game.cso"), ("saturn", "Game.cue"),
    ("dreamcast", "Game.gdi"),
])
def test_new_system_archive_selects_playable_descriptor(
        source, tmp_path, monkeypatch, system, launch_name):
    candidate, _ = source
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr(launch_name, b"descriptor")
        if launch_name.endswith((".cue", ".gdi")):
            z.writestr("track.bin", b"disc data")
    data = payload.getvalue()
    # Exercise the real downloader against a local HTTP source, not a fake ROM.
    class Response(io.BytesIO):
        def __init__(self):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}
    monkeypatch.setattr(adapter.urllib.request, "urlopen", lambda *args, **kw: Response())
    candidate()
    cached = adapter._load_cache()
    cached["game"]["system"] = system
    adapter._save_cache(cached)
    result = adapter.download("game")
    assert result["system"] == system
    assert result["filename"] == launch_name
    assert {f["filename"] for f in result["files"]} == (
        {launch_name, "track.bin"} if launch_name.endswith((".cue", ".gdi"))
        else {launch_name})


def test_psp_package_can_only_be_acquired(source, monkeypatch):
    candidate, _ = source
    candidate()
    cached = adapter._load_cache()
    cached["game"].update(system="psp", title="Game.pkg")
    adapter._save_cache(cached)
    class Response(io.BytesIO):
        headers = {"Content-Length": "4"}
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *args, **kw: Response(b"\x7fPKG"))
    result = adapter.download("game")
    assert result["filename"] == "Game.pkg"
    assert result["sha256"] == hashlib.sha256(b"\x7fPKG").hexdigest()
    assert ".pkg" not in adapter.LAUNCH_EXTENSIONS["psp"]


def test_vita_package_can_be_acquired_without_claiming_playability(source, monkeypatch):
    candidate, _ = source
    candidate()
    cached = adapter._load_cache()
    cached["game"].update(system="psvita", title="Game.pkg")
    adapter._save_cache(cached)
    class Response(io.BytesIO):
        headers = {"Content-Length": "4"}
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *args, **kw: Response(b"\x7fPKG"))
    result = adapter.download("game")
    assert result["system"] == "psvita"
    assert result["filename"] == "Game.pkg"
    assert result["sha256"] == hashlib.sha256(b"\x7fPKG").hexdigest()
    assert ".pkg" not in adapter.LAUNCH_EXTENSIONS.get("psvita", set())
