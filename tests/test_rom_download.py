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
