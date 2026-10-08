"""Jobs queued by bare candidate id must be labelled with the game's title.

`arcadectl roms request --id ID` sends no title, which used to leave the
candidate id as the queue row's title — and let ids the downloader could not
resolve queue up only to fail later.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import arcaded  # noqa: E402
import romdl  # noqa: E402


class Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def queue(monkeypatch):
    spawned = []

    async def fake_worker(entry):
        spawned.append(entry["id"])

    monkeypatch.setattr(arcaded, "rom_queue", [])
    monkeypatch.setattr(arcaded, "rom_worker", fake_worker)
    monkeypatch.setattr(arcaded, "save_rom_queue", lambda: None)
    monkeypatch.setattr(romdl, "using_real", lambda: True)
    monkeypatch.setattr(romdl, "_load_cache", lambda: {
        "abc123": {"title": "Contra (USA).zip", "system": "nes"}})
    monkeypatch.setattr(arcaded, "_rom_system", lambda s: s)
    return spawned


def request(body):
    async def go():
        res = await arcaded.roms_request(Req(body))
        await asyncio.sleep(0)          # let the spawned worker task run
        return res
    return asyncio.run(go())


def test_bare_id_request_takes_title_and_system_from_the_cache(queue):
    res = request({"id": "abc123"})
    entry = json.loads(res.body)
    assert entry["title"] == "Contra (USA).zip"
    assert entry["system"] == "nes"
    assert queue == ["abc123"]


def test_explicit_title_still_wins(queue):
    entry = json.loads(request({"id": "abc123", "title": "Contra"}).body)
    assert entry["title"] == "Contra"


def test_unresolvable_id_is_refused_instead_of_queued(queue):
    res = request({"id": "deadbeefdeadbeef", "system": "nes"})
    assert res.status == 404
    assert "search again" in json.loads(res.body)["error"]
    assert arcaded.rom_queue == [] and queue == []


def test_existing_bare_titles_are_relabelled_and_unknown_ones_left_alone(queue):
    arcaded.rom_queue.extend([
        {"id": "abc123", "title": "abc123", "system": "", "status": "queued",
         "detail": ""},
        {"id": "ffffffffffffffff", "title": "ffffffffffffffff", "system": "psx",
         "status": "error", "detail": "KeyError('unknown rom id')"},
        {"id": "q1", "title": "Pepsiman", "system": "psx", "status": "queued",
         "detail": ""}])
    arcaded.label_bare_queue_titles()
    titles = [e["title"] for e in arcaded.rom_queue]
    assert titles == ["Contra (USA).zip", "ffffffffffffffff", "Pepsiman"]
    assert arcaded.rom_queue[0]["system"] == "nes"
