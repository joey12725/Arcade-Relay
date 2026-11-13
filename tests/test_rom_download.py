"""Server-side ROM staging: nothing unverified ever becomes an installable file."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import types
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("adapter", Path(__file__).parents[1] / "server/romdl.py")
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


def _zip(members):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return payload.getvalue()


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A staging dir plus an owner library at ROM_SOURCE_DIR/<system>/."""
    staging, src = tmp_path / "staging", tmp_path / "library-src"
    staging.mkdir()
    src.mkdir()
    data = _zip({"Game (USA).nes": b"NES\x1a" + b"rom bytes" * 200})
    monkeypatch.setattr(adapter, "STAGING", str(staging))
    monkeypatch.setattr(adapter, "SOURCE_DIR", str(src))
    monkeypatch.setattr(adapter, "BACKEND", "library")
    monkeypatch.setattr(adapter.shutil, "disk_usage",
                        lambda p: types.SimpleNamespace(free=100 * 1024 ** 3))

    def candidate(system="nes", name="Game (USA).zip", payload=data, **kw):
        path = src / system / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(payload)
        adapter._save_cache({"game": {"title": name, "system": system,
                                      "url": path.resolve().as_uri(), **kw}})
        return path
    return types.SimpleNamespace(candidate=candidate, data=data, staging=staging, src=src)


def test_truncated_copy_preserves_existing_archive(source, monkeypatch):
    source.candidate()
    (source.staging / "game").mkdir()
    existing = source.staging / "game" / "Game (USA).zip"
    existing.write_bytes(b"existing")

    class Short(io.BytesIO):
        headers = {"Content-Length": str(len(source.data))}
    monkeypatch.setattr(adapter.urllib.request, "urlopen",
                        lambda *a, **kw: Short(source.data[:len(source.data) // 2]))
    with pytest.raises((RuntimeError, OSError)):
        adapter.download("game")
    assert existing.read_bytes() == b"existing"
    assert not list(source.staging.rglob("*.part"))


def test_source_checksum_and_extracted_cartridge(source):
    source.candidate(sha256=hashlib.sha256(source.data).hexdigest())
    result = adapter.download("game")
    assert result["filename"] == "Game (USA).nes"
    assert Path(result["rom_path"]).read_bytes().startswith(b"NES\x1a")
    assert result["sha256"] == hashlib.sha256(Path(result["rom_path"]).read_bytes()).hexdigest()
    assert result["files"][0]["sha256"] == result["sha256"]
    assert result["source_metadata"]["sha256"] == hashlib.sha256(source.data).hexdigest()


def test_wrong_source_checksum_never_publishes(source):
    source.candidate(sha256="0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        adapter.download("game")
    assert not (source.staging / "game" / "Game (USA).zip").exists()
    assert not list(source.staging.rglob("*.part"))


def test_retained_verified_archive_reinstalls_without_touching_source(source):
    path = source.candidate()
    cache = adapter._load_cache()
    cache["game"]["source_metadata"] = {"file_name": "Game (USA).zip", "size": len(source.data),
                                        "sha256": hashlib.sha256(source.data).hexdigest()}
    adapter._save_cache(cache)
    (source.staging / "game").mkdir()
    (source.staging / "game/Game (USA).zip").write_bytes(source.data)
    path.unlink()   # the library copy is gone; the retained one must suffice
    result = adapter.download("game")
    assert result["filename"] == "Game (USA).nes"
    assert result["source_metadata"]["sha256"] == hashlib.sha256(source.data).hexdigest()
    assert result["source_reused"] is True


def test_search_refresh_preserves_verified_source_metadata(source):
    path = source.candidate()
    rid = adapter._candidate_id(path.resolve().as_uri())
    metadata = {"file_name": "Game (USA).zip", "size": len(source.data),
                "sha256": hashlib.sha256(source.data).hexdigest()}
    adapter._save_cache({rid: {"title": "Game (USA).zip", "source_metadata": metadata}})
    adapter.search("Game", "nes")
    assert adapter._load_cache()[rid]["source_metadata"] == metadata


def test_search_ranks_exact_titles_first_and_stays_in_system(source):
    for name in ("Game (USA).zip", "Game II (USA).zip", "Other Thing (Europe).zip", "notes.txt"):
        (source.src / "nes").mkdir(exist_ok=True)
        (source.src / "nes" / name).write_bytes(b"x")
    (source.src / "snes").mkdir()
    (source.src / "snes" / "Game (USA).sfc").write_bytes(b"x")
    results = adapter.search("game", "nes")
    titles = [r["title"] for r in results]
    assert titles[0] == "Game (USA).zip" and results[0]["score"] == 150
    assert "notes.txt" not in titles and "Game (USA).sfc" not in titles
    assert all(r["system"] == "nes" and r["source"] == "library" for r in results)
    assert results[-1]["score"] < 100   # fuzzy-only matches need a human pick


def test_search_follows_system_aliases(source):
    (source.src / "megadrive").mkdir()
    (source.src / "megadrive" / "Demo (World).md").write_bytes(b"x")
    results = adapter.search("demo", "genesis")
    assert [r["title"] for r in results] == ["Demo (World).md"]
    assert results[0]["system"] == "genesis"


def test_search_requires_a_system(source):
    with pytest.raises(ValueError):
        adapter.search("game")


@pytest.mark.parametrize("query,title,confident", [
    ("Game", "Game (USA).zip", True),
    ("game ii", "Game II (USA) (Rev 1).zip", True),
    ("game", "Game II (USA).zip", True),
    ("gmae", "Game (USA).zip", False),
])
def test_score_threshold(query, title, confident):
    assert (adapter.score(query, title) >= 100) is confident


@pytest.mark.parametrize("system,name", [
    ("gbc", "Game.gbc"), ("n64", "Game.z64"), ("nds", "Game.nds"),
    ("3ds", "Game.cia"), ("psp", "Game.pkg"), ("psvita", "Game.vpk"),
    ("ps2", "Game.iso"), ("saturn", "Game.chd"), ("dreamcast", "Game.cdi"),
])
def test_library_formats(source, system, name):
    source.candidate(system=system, name="Game.zip", payload=_zip({name: b"payload"}))
    result = adapter.download("game")
    assert result["filename"] == name
    assert result["sha256"] == hashlib.sha256(b"payload").hexdigest()
    if name.endswith((".cia", ".pkg", ".vpk")):
        assert Path(name).suffix not in adapter.LAUNCH_EXTENSIONS.get(system, set())


def test_bare_cartridge_is_staged_as_is(source):
    source.candidate(system="gba", name="Homebrew Demo.gba", payload=b"GBA" * 100)
    result = adapter.download("game")
    assert result["filename"] == "Homebrew Demo.gba"
    assert Path(result["rom_path"]).read_bytes() == b"GBA" * 100


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


def test_server_reserve_rejects_before_payload(source, monkeypatch):
    source.candidate()
    monkeypatch.setattr(adapter.shutil, "disk_usage",
                        lambda p: types.SimpleNamespace(free=adapter.MIN_FREE_BYTES - 1))
    with pytest.raises(RuntimeError, match="storage reserve"):
        adapter.download("game")
    assert not list(source.staging.rglob("*.part"))


def test_only_source_dir_files_can_be_delivered(source, tmp_path):
    source.candidate()
    outside = tmp_path / "elsewhere.zip"
    outside.write_bytes(source.data)
    escape = source.src / "nes" / "escape.zip"
    os.symlink(outside, escape)
    for url in ("https://example.invalid/Game.zip", outside.as_uri(),
                escape.as_uri()):
        cache = adapter._load_cache()
        cache["game"]["url"] = url
        adapter._save_cache(cache)
        with pytest.raises(RuntimeError, match="ROM_SOURCE_DIR"):
            adapter.download("game")
    assert "escape.zip" not in [r["title"] for r in adapter.search("escape", "nes")]


def test_cancel_removes_partial(source):
    import threading
    source.candidate()
    stop = threading.Event()
    stop.set()
    with pytest.raises(adapter.Cancelled):
        adapter.download("game", cancel=stop)
    assert not list(source.staging.rglob("*.part"))


def test_backend_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "BACKEND", "auto")
    monkeypatch.setattr(adapter, "SOURCE_DIR", None)
    assert adapter.backend_name() == "fake"
    monkeypatch.setattr(adapter, "SOURCE_DIR", str(tmp_path))
    assert adapter.backend_name() == "library"
    monkeypatch.setattr(adapter, "BACKEND", "fake")
    assert adapter.backend_name() == "fake"


@pytest.mark.parametrize("system,name,descriptor", [
    ("saturn", "Game.cue", 'FILE "track.bin" BINARY\n TRACK 01 MODE1/2352\n'),
    ("dreamcast", "Game.gdi", '1\n1 0 4 2352 "track.bin" 0\n'),
])
def test_disc_archive_hashes_descriptor_and_every_track(source, system, name, descriptor):
    members = {name: descriptor.encode(), "track.bin": b"track contents"}
    source.candidate(system=system, name="Game.zip", payload=_zip(members))
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


def test_download_owns_staged_files(source, monkeypatch):
    source.candidate(sha256=hashlib.sha256(source.data).hexdigest())
    chowned = []
    monkeypatch.setattr(adapter, "OWNER", "99:100")
    monkeypatch.setattr(adapter.os, "chown", lambda p, uid, gid: chowned.append(Path(p)))
    adapter._save_cache(adapter._load_cache())
    result = adapter.download("game")
    owned = set(chowned)
    st = source.staging
    assert {st / "game", st / "game" / "Game (USA).zip",
            Path(result["rom_path"]), st / "candidates.json"} <= owned


def test_download_without_owner_never_chowns(source, monkeypatch):
    def unexpected(*a):
        raise AssertionError("chown without ROM_LIBRARY_OWNER")
    monkeypatch.setattr(adapter.os, "chown", unexpected)
    source.candidate(sha256=hashlib.sha256(source.data).hexdigest())
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
    monkeypatch.setattr(ad.romdl, "BACKEND", "library")
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
