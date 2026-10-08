"""Acquisition must not imply launch support or accept mismatched candidates."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "cabinet"))


class Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    ad = importlib.import_module("arcaded")
    monkeypatch.setattr(ad, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ad.romdl, "BACKEND", "real")
    (tmp_path / "cabinet.yaml").write_text(
        "systems: [nes, psp, 3ds]\nemulators:\n"
        "  nes: {core: installed}\n  psp: {core: installed}\n"
        "  3ds: {core: installed}\ndownloads:\n  systems: [gbc, psvita]\n")
    monkeypatch.setattr(ad, "save_rom_queue", lambda: None)
    monkeypatch.setattr(ad, "rom_cancel_events", {})
    return ad


def test_eligibility_and_backend_separate_downloads(server):
    assert server._rom_system("vita") == "psvita"
    assert server._rom_system("gbc") == "gbc"
    with pytest.raises(ValueError):
        server._rom_system("xbox")
    data = json.loads(asyncio.run(server.roms_backend(None)).text)
    assert "psvita" in data["acquisition_only_systems"]
    assert "psvita" not in data["playable_systems"]
    assert "xbox" not in data["systems"]


@pytest.mark.parametrize("system,name", [("psp", "Game.pkg"), ("3ds", "Game.cia"), ("psvita", "Game.vpk")])
def test_package_job_is_verified_without_launcher_actions(server, tmp_path, monkeypatch, system, name):
    payload = {"system": system, "filename": name, "title": "Game",
               "rom_path": str(tmp_path / name), "sha256": "a" * 64}
    monkeypatch.setattr(server.romdl, "download", lambda *a: payload)
    rpc = AsyncMock(return_value={"ok": True, "result": {"sha256": "a" * 64}})
    monkeypatch.setattr(server, "hub", types.SimpleNamespace(
        online=True, rpc=rpc, log_event=lambda msg: None))
    def unexpected(*a):
        raise AssertionError("acquisition-only must not register launcher metadata")
    monkeypatch.setattr(server, "_register_installed_rom", unexpected)
    monkeypatch.setattr(server, "_deliver_art", unexpected)
    job = {"id": "job", "candidate_id": "candidate", "system": system, "title": "Game"}
    asyncio.run(server._rom_worker(job))
    assert job["status"] == "ready"
    assert job["verified"] is True
    assert job["playable"] is False
    assert job["acquisition_only"] is True
    assert [c.args[0] for c in rpc.call_args_list] == ["file.fetch"]
    assert rpc.call_args.args[1]["min_free_bytes"] == 40 * 1024 ** 3


def test_exact_request_rejects_cross_system_before_queue(server, monkeypatch):
    monkeypatch.setattr(server.romdl, "describe", lambda rid: {"title": "Wrong", "system": "nes"})
    response = asyncio.run(server.roms_request(Request({"id": "candidate", "system": "gbc"})))
    assert response.status == 400


def test_delivery_requires_checksum_confirmation(server, tmp_path, monkeypatch):
    monkeypatch.setattr(server.romdl, "download", lambda *a: {
        "system": "gbc", "filename": "Game.gbc", "title": "Game",
        "rom_path": str(tmp_path / "Game.gbc"), "sha256": "a" * 64})
    monkeypatch.setattr(server, "hub", types.SimpleNamespace(
        online=True, rpc=AsyncMock(return_value={"ok": True, "result": {}}),
        log_event=lambda msg: None))
    job = {"id": "job", "candidate_id": "candidate", "system": "gbc", "title": "Game"}
    asyncio.run(server._rom_worker(job))
    assert job["status"] == "error"
    assert "checksum" in job["detail"]
    assert "verified" not in job


def test_cabinet_reserve_and_cached_file(tmp_path, monkeypatch):
    import types
    cabd = importlib.import_module("cabd")
    path = tmp_path / "Game.gbc"
    class Response:
        status = 200
        content_length = 20
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def get(self, url):
            return Response()
    monkeypatch.setattr(cabd.aiohttp, "ClientSession", lambda **kw: Session())
    monkeypatch.setattr("shutil.disk_usage", lambda p: types.SimpleNamespace(free=50))
    with pytest.raises(RuntimeError, match="storage reserve"):
        asyncio.run(cabd.file_fetch("/file", str(path), min_free_bytes=40))
    assert not path.exists()
    assert not Path(str(path) + ".part").exists()
    import hashlib
    path.write_bytes(b"same bytes")
    before = path.stat().st_mtime_ns
    result = asyncio.run(cabd.file_fetch("/file", str(path),
        hashlib.sha256(b"same bytes").hexdigest(), True, 40))
    assert result["cached"] is True
    assert path.stat().st_mtime_ns == before
