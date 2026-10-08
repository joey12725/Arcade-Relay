"""A large ROM cannot consume the cabinet's reserved free space."""
import asyncio
import importlib.util
from pathlib import Path
import types


def test_delivery_rejects_file_larger_than_available_budget(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "cabd_reserve", Path(__file__).parents[1] / "cabinet/cabd.py")
    cabd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cabd)

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
    monkeypatch.setattr("shutil.disk_usage", lambda path: types.SimpleNamespace(free=50))
    target = tmp_path / "n64" / "Game.z64"
    try:
        asyncio.run(cabd.file_fetch("/roms/files/test", str(target),
                                    min_free_bytes=40))
    except RuntimeError as exc:
        assert "storage reserve" in str(exc)
    else:
        raise AssertionError("oversized delivery was accepted")
    assert not target.exists()
    assert not target.with_suffix(".z64.part").exists()
