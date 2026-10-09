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


# ------------------------------------------------------------ library promotion

@pytest.fixture
def library(tmp_path, monkeypatch):
    staging, lib = tmp_path / "staging", tmp_path / "library"
    staging.mkdir()
    lib.mkdir()
    chowned = []
    monkeypatch.setattr(adapter, "STAGING", str(staging))
    monkeypatch.setattr(adapter, "LIBRARY", str(lib))
    monkeypatch.setattr(adapter, "OWNER", "99:100")
    monkeypatch.setattr(adapter.os, "chown", lambda p, uid, gid: chowned.append((Path(p), uid, gid)))
    def staged(rid, system, members, primary=None):
        item = staging / rid
        item.mkdir()
        (item / "Source.zip").write_bytes(b"source archive")
        files = []
        for name, data in members.items():
            (item / name).write_bytes(data)
            files.append({"filename": name, "rom_path": str(item / name),
                          "sha256": hashlib.sha256(data).hexdigest()})
        main = next(f for f in files if f["filename"] == (primary or files[0]["filename"]))
        return {**main, "system": system, "title": "Game", "files": files if len(files) > 1 else []}
    return types.SimpleNamespace(staging=staging, lib=lib, chowned=chowned, staged=staged)


def test_promote_single_payload_into_system_folder(library):
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    paths = adapter.promote(result)
    dest = library.lib / "gb" / "Game.gb"
    assert paths == [str(dest)]
    assert dest.read_bytes() == b"gb rom"
    assert not (library.staging / "abc").exists()
    assert (dest, 99, 100) in library.chowned
    assert (library.lib / "gb", 99, 100) in library.chowned
    assert dest.stat().st_mode & 0o777 == 0o666
    assert (library.lib / "gb").stat().st_mode & 0o777 == 0o777


def test_promote_cue_and_bin_together(library):
    result = library.staged("def", "psx", {"Game.cue": b'FILE "Game.bin" BINARY', "Game.bin": b"track"},
                            primary="Game.cue")
    paths = adapter.promote(result)
    assert sorted(Path(p).name for p in paths) == ["Game.bin", "Game.cue"]
    assert (library.lib / "psx" / "Game.bin").read_bytes() == b"track"
    assert not (library.staging / "def").exists()


def test_promote_same_bytes_already_in_library_drops_staging(library):
    (library.lib / "gb").mkdir()
    (library.lib / "gb" / "Game.gb").write_bytes(b"gb rom")
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    assert adapter.promote(result) == [str(library.lib / "gb" / "Game.gb")]
    assert not (library.staging / "abc").exists()


def test_promote_conflict_moves_nothing(library):
    (library.lib / "psx").mkdir()
    (library.lib / "psx" / "Game.bin").write_bytes(b"another dump")
    result = library.staged("def", "psx", {"Game.cue": b'FILE "Game.bin" BINARY', "Game.bin": b"track"},
                            primary="Game.cue")
    with pytest.raises(adapter.LibraryConflict):
        adapter.promote(result)
    assert (library.lib / "psx" / "Game.bin").read_bytes() == b"another dump"
    assert not (library.lib / "psx" / "Game.cue").exists()
    assert sorted(p.name for p in (library.staging / "def").iterdir()) == ["Game.bin", "Game.cue", "Source.zip"]


def test_promote_without_library_is_a_no_op(library, monkeypatch):
    monkeypatch.setattr(adapter, "LIBRARY", None)
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    assert adapter.promote(result) == []
    assert (library.staging / "abc" / "Game.gb").exists()
    assert not list(library.lib.iterdir())


@pytest.mark.parametrize("system,name", [
    ("../gb", "Game.gb"), ("GB", "Game.gb"), ("", "Game.gb"),
    ("gb", "../Game.gb"), ("gb", "sub/Game.gb"), ("gb", ".."), ("gb", "."),
])
def test_promote_refuses_unsafe_names(library, system, name):
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    result["system"], result["filename"] = system, name
    with pytest.raises(ValueError):
        adapter.promote(result)
    assert (library.staging / "abc" / "Game.gb").exists()
    assert not list(library.lib.iterdir())


def test_promote_copies_and_verifies_across_filesystems(library, monkeypatch):
    real_replace = adapter.os.replace
    def replace(src, dst):
        if Path(src).parent.parent == library.staging:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(src, dst)
    monkeypatch.setattr(adapter.os, "replace", replace)
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    adapter.promote(result)
    assert (library.lib / "gb" / "Game.gb").read_bytes() == b"gb rom"
    assert not list(library.lib.rglob("*.part"))
    assert not (library.staging / "abc").exists()


def test_own_failure_is_logged_not_raised(library, monkeypatch, capsys):
    def refuse(*a):
        raise PermissionError("not permitted")
    monkeypatch.setattr(adapter.os, "chown", refuse)
    result = library.staged("abc", "gb", {"Game.gb": b"gb rom"})
    adapter.promote(result)
    assert (library.lib / "gb" / "Game.gb").exists()
    assert "cannot own" in capsys.readouterr().err


def test_download_owns_staged_files(source, tmp_path, monkeypatch):
    candidate, data = source
    chowned = []
    monkeypatch.setattr(adapter, "OWNER", "99:100")
    monkeypatch.setattr(adapter.os, "chown", lambda p, uid, gid: chowned.append(Path(p)))
    candidate(sha256=hashlib.sha256(data).hexdigest())
    result = adapter.download("game")
    owned = set(chowned)
    assert {tmp_path / "game", tmp_path / "game" / "Game (USA).zip",
            Path(result["rom_path"]), tmp_path / "candidates.json"} <= owned


def test_download_without_owner_never_chowns(source, monkeypatch):
    candidate, data = source
    def unexpected(*a):
        raise AssertionError("chown without ROM_LIBRARY_OWNER")
    monkeypatch.setattr(adapter.os, "chown", unexpected)
    candidate(sha256=hashlib.sha256(data).hexdigest())
    adapter.download("game")


@pytest.fixture
def worker(tmp_path, monkeypatch):
    import asyncio
    import importlib
    import sys
    from unittest.mock import AsyncMock
    sys.path.insert(0, str(Path(__file__).parents[1] / "server"))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    ad = importlib.import_module("arcaded")
    monkeypatch.setattr(ad, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ad.romdl, "BACKEND", "real")
    (tmp_path / "cabinet.yaml").write_text("systems: [gb]\nemulators:\n  gb: {core: installed}\n")
    monkeypatch.setattr(ad, "save_rom_queue", lambda: None)
    monkeypatch.setattr(ad, "rom_cancel_events", {})
    monkeypatch.setattr(ad, "_register_installed_rom", lambda entry: None)
    monkeypatch.setattr(ad, "_deliver_art", AsyncMock(return_value=True))
    monkeypatch.setattr(ad, "build_bundle", lambda: {})
    events = []
    rpc = AsyncMock(return_value={"ok": True, "result": {"sha256": "a" * 64}})
    monkeypatch.setattr(ad, "hub", types.SimpleNamespace(online=True, rpc=rpc, log_event=events.append))
    payload = {"system": "gb", "filename": "Game.gb", "title": "Game",
               "rom_path": str(tmp_path / "Game.gb"), "sha256": "a" * 64}
    monkeypatch.setattr(ad.romdl, "download", lambda *a: payload)
    def run():
        job = {"id": "job", "candidate_id": "candidate", "system": "gb", "title": "Game"}
        asyncio.run(ad._rom_worker(job))
        return job
    return types.SimpleNamespace(ad=ad, run=run, events=events)


def test_worker_records_library_paths(worker, monkeypatch):
    monkeypatch.setattr(worker.ad.romdl, "LIBRARY", "/library")
    monkeypatch.setattr(worker.ad.romdl, "promote", lambda files: ["/library/gb/Game.gb"])
    job = worker.run()
    assert job["status"] == "ready"
    assert job["library_paths"] == ["/library/gb/Game.gb"]
    assert "library_error" not in job


def test_worker_promotion_error_keeps_entry_ready(worker, monkeypatch):
    monkeypatch.setattr(worker.ad.romdl, "LIBRARY", "/library")
    def conflict(files):
        raise worker.ad.romdl.LibraryConflict("/library/gb/Game.gb already holds a different file")
    monkeypatch.setattr(worker.ad.romdl, "promote", conflict)
    job = worker.run()
    assert job["status"] == "ready"
    assert "LibraryConflict" in job["library_error"]
    assert "library_paths" not in job
    assert any("promotion failed" in e for e in worker.events)


def test_worker_without_library_skips_promotion(worker, monkeypatch):
    monkeypatch.setattr(worker.ad.romdl, "LIBRARY", None)
    def unexpected(files):
        raise AssertionError("promote without ROM_LIBRARY")
    monkeypatch.setattr(worker.ad.romdl, "promote", unexpected)
    job = worker.run()
    assert job["status"] == "ready"
    assert "library_paths" not in job and "library_error" not in job
