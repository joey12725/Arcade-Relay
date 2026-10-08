"""Transport faults must never become installable files."""
import hashlib
import importlib.util
import io
from pathlib import Path
import threading
import types
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
    monkeypatch.setattr(adapter.shutil, "disk_usage",
                        lambda p: types.SimpleNamespace(free=100 * 1024 ** 3))
    def fetch(entry, path, cancel, progress):
        with adapter.urllib.request.urlopen(entry["test_url"]) as response:
            chunks = []
            while chunk := response.read(65536):
                chunks.append(chunk)
            Path(path).write_bytes(b"".join(chunks))
        # Model a provider with independently published expected bytes.
        return {"file_name": entry["title"], "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(adapter, "_fetch_torrent", fetch)
    def candidate(path="/good", **kw):
        adapter._save_cache({"game": {"title": "Game (USA).zip", "system": "nes",
                            "url": "https://mirror.invalid/rom?id=test",
                            "test_url": f"http://127.0.0.1:{http.server_port}{path}", **kw}})
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


@pytest.mark.parametrize("system,name", [
    ("gbc", "Game.gbc"), ("n64", "Game.z64"), ("nds", "Game.nds"),
    ("3ds", "Game.cia"), ("psp", "Game.pkg"), ("psvita", "Game.vpk"),
    ("ps2", "Game.iso"), ("saturn", "Game.chd"), ("dreamcast", "Game.cdi"),
])
def test_acquisition_formats(source, monkeypatch, system, name):
    candidate, _ = source
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        z.writestr(name, b"payload")
    data = payload.getvalue()
    monkeypatch.setattr(adapter, "_fetch_torrent", lambda entry, path, *a:
        (Path(path).write_bytes(data) and {"file_name": entry["title"], "size": len(data),
         "sha256": hashlib.sha256(data).hexdigest()}))
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(data))}
    monkeypatch.setattr(adapter.urllib.request, "urlopen", lambda *a, **kw: Response(data))
    candidate()
    cache = adapter._load_cache()
    cache["game"]["system"] = system
    adapter._save_cache(cache)
    result = adapter.download("game")
    assert result["filename"] == name
    assert result["sha256"] == hashlib.sha256(b"payload").hexdigest()
    if name.endswith((".cia", ".pkg", ".vpk")):
        assert Path(name).suffix not in adapter.LAUNCH_EXTENSIONS.get(system, set())


@pytest.mark.parametrize("name,descriptor", [
    ("Game.cue", 'FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n'),
    ("Game.gdi", '1\n1 0 4 2352 "track.bin" 0\n'),
])
def test_descriptor_requires_manifest_tracks(tmp_path, name, descriptor):
    path = tmp_path / name
    path.write_text(descriptor)
    with pytest.raises(RuntimeError, match="missing or unsafe"):
        adapter.validate_descriptor(path, [])
    adapter.validate_descriptor(path, [{"filename": "track.bin"}])


def test_server_reserve_rejects_before_payload(source, tmp_path, monkeypatch):
    import types
    candidate, _ = source
    candidate()
    monkeypatch.setattr(adapter.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=adapter.MIN_FREE_BYTES - 1))
    with pytest.raises(RuntimeError, match="storage reserve"):
        adapter.download("game")
    assert not list(tmp_path.rglob("*.part"))


def test_search_drops_cross_system_results(source, monkeypatch):
    monkeypatch.setattr(adapter, "_run_romdl", lambda *a: [
        {"title": "Game.zip", "system": "psvita", "url": "https://example.invalid/wrong"},
        {"title": "Game.zip", "console": "n64", "url": "https://example.invalid/right"},
    ])
    results = adapter.search("Game", "n64")
    assert len(results) == 1
    assert results[0]["id"] == adapter._candidate_id("https://example.invalid/right")


def test_direct_provider_payload_cannot_bypass_vpn(source):
    candidate, _ = source
    candidate()
    cache = adapter._load_cache()
    cache["game"]["url"] = "https://example.invalid/Game.pkg"
    adapter._save_cache(cache)
    with pytest.raises(RuntimeError, match="server-side"):
        adapter.download("game")


@pytest.mark.parametrize("system,name,descriptor", [
    ("saturn", "Game.cue", 'FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n'),
    ("dreamcast", "Game.gdi", '1\n1 0 4 2352 "track.bin" 0\n'),
])
def test_disc_archive_hashes_descriptor_and_every_track(source, monkeypatch, system, name, descriptor):
    candidate, _ = source
    payload = io.BytesIO()
    members = {name: descriptor.encode(), "track.bin": b"track contents"}
    with zipfile.ZipFile(payload, "w") as z:
        for filename, data in members.items():
            z.writestr(filename, data)
    archive = payload.getvalue()
    monkeypatch.setattr(adapter, "_fetch_torrent", lambda entry, path, *a:
        (Path(path).write_bytes(archive) and {"file_name": entry["title"], "size": len(archive),
         "sha256": hashlib.sha256(archive).hexdigest()}))
    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(archive))}
    monkeypatch.setattr(adapter.urllib.request, "urlopen", lambda *a, **kw: Response(archive))
    candidate()
    cache = adapter._load_cache()
    cache["game"]["system"] = system
    adapter._save_cache(cache)
    result = adapter.download("game")
    assert result["filename"] == name
    assert {f["filename"]: f["sha256"] for f in result["files"]} == {
        filename: hashlib.sha256(data).hexdigest() for filename, data in members.items()}
