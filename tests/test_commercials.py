"""Commercial policy validation, queue boundaries and YouTube failure isolation."""
import asyncio
import json
import pathlib
import random
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import commercials
from test_playlists import ad, call, write


@pytest.mark.parametrize("count", range(6))
def test_break_count_and_loop_boundary(count):
    episodes = ["/media/a", "/media/b"]
    ids = ["g5WlcjXiZZk", "abcdefghijk", "01234567890"]
    out = commercials.interleave(episodes, ids, count, random.Random(1))
    assert len(out) == len(episodes) * (count + 1)
    for i, episode in enumerate(episodes):
        assert out[i * (count + 1)] == episode
        assert all(p.startswith("/commercials/") for p in
                   out[i * (count + 1) + 1:(i + 1) * (count + 1)])
    ads = [p for p in out if p.startswith("/commercials/")]
    assert len(set(ads[:3])) == min(3, len(ads))


def test_no_sources_or_episodes():
    assert commercials.interleave(["/media/a"], [], 5) == ["/media/a"]
    assert commercials.interleave([], ["abcdefghijk"], 5) == []


@pytest.mark.parametrize("count", [-1, 6, 1.5, True, "2", None])
def test_reject_invalid_count_without_saving(ad, count):
    before = ad.load_screensaver()
    status, result = call(ad.screensaver_set, {"commercial_count": count})
    assert status == 400
    assert ad.load_screensaver() == before


def test_source_validation_and_canonicalization(ad):
    status, cfg = call(ad.screensaver_set, {"commercial_sources": [
        {"url": "https://youtu.be/g5WlcjXiZZk?tracking=1", "enabled": True},
        {"url": "https://www.youtube.com/watch?v=g5WlcjXiZZk", "enabled": False}]})
    assert status == 200
    assert cfg["commercial_sources"] == [{"url": "https://www.youtube.com/watch?v=g5WlcjXiZZk", "enabled": True}]
    for sources in [None, ["https://youtu.be/g5WlcjXiZZk"],
                    [{"url": "https://example.com/watch?v=g5WlcjXiZZk", "enabled": True}],
                    [{"url": "https://youtu.be/g5WlcjXiZZk", "enabled": "false"}]]:
        assert call(ad.screensaver_set, {"commercial_sources": sources})[0] == 400
    assert ad.load_screensaver()["commercial_sources"] == cfg["commercial_sources"]


def test_only_selected_sources_and_failed_playlist(monkeypatch):
    seen = []
    async def videos(url):
        seen.append(url)
        if url == "broken":
            raise RuntimeError("unavailable")
        return ["g5WlcjXiZZk"]
    monkeypatch.setattr(commercials, "videos", videos)
    events = []
    ids = asyncio.run(commercials.pool([
        {"url": "selected", "enabled": True},
        {"url": "disabled", "enabled": False},
        {"url": "broken", "enabled": True}], events.append))
    assert seen == ["selected", "broken"]
    assert ids == ["g5WlcjXiZZk"]
    assert "unavailable" in events[0]


def test_enabled_playlist_interleaves_but_offline_cache_uses_episodes(ad, monkeypatch):
    seen = []
    async def pool(sources, log):
        seen.append(sources)
        return ["g5WlcjXiZZk"]
    monkeypatch.setattr(commercials, "pool", pool)
    status, cfg = call(ad.screensaver_set, {"commercial_count": 2})
    assert status == 200
    status, pl = call(ad.screensaver_playlist)
    assert pl["playlist"] == ["/media/1-e0", "/commercials/g5WlcjXiZZk", "/commercials/g5WlcjXiZZk",
                              "/media/1-e1", "/commercials/g5WlcjXiZZk", "/commercials/g5WlcjXiZZk"]
    assert pl["episode_playlist"] == ["/media/1-e0", "/media/1-e1"]
    assert seen == [cfg["commercial_sources"]]
    call(ad.screensaver_set, {"commercial_count": 0})
    assert call(ad.screensaver_playlist)[1]["playlist"] == pl["episode_playlist"]
    assert len(seen) == 1


def test_changed_commercial_settings_restart_only_once(ad):
    ad.hub.cab_state["screensaver"] = True
    sources = [{"url": "https://youtu.be/g5WlcjXiZZk", "enabled": True}]
    status, result = call(ad.screensaver_set, {"commercial_count": 1, "commercial_sources": sources})
    assert status == 200
    assert len(ad.hub.calls) == 1
    assert "/commercials/g5WlcjXiZZk" in ad.hub.calls[0][1]["playlist"]
    call(ad.screensaver_set, {"commercial_count": 1, "commercial_sources": sources})
    assert len(ad.hub.calls) == 1


def test_catalog_filters_missing_and_caches(monkeypatch):
    calls = []
    async def run(*args, **kwargs):
        calls.append(args)
        return json.dumps({"entries": [{"id": "g5WlcjXiZZk", "title": "Ad"},
                           {"id": "abcdefghijk", "title": "[Private video]"}, None,
                           {"id": "bad"}, {"id": "g5WlcjXiZZk"}]})
    monkeypatch.setattr(commercials, "run", run)
    monkeypatch.setattr(commercials, "_catalog", {})
    monkeypatch.setattr(commercials, "_locks", {})
    async def check():
        url = "https://www.youtube.com/playlist?list=test"
        assert await commercials.videos(url) == ["g5WlcjXiZZk"]
        assert await commercials.videos(url) == ["g5WlcjXiZZk"]
    asyncio.run(check())
    assert len(calls) == 1


def test_cache_download_transcodes_once_and_evicts_old_file(tmp_path, monkeypatch):
    monkeypatch.setattr(commercials, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(commercials, "CACHE_BYTES", 5)
    monkeypatch.setattr(commercials, "_locks", {})
    monkeypatch.setattr(commercials, "_download_slots", asyncio.Semaphore(2))
    old = tmp_path / "old.mkv"
    old.write_bytes(b"123456")
    calls = []
    async def run(*args, **kwargs):
        calls.append(args)
        if args[0] == commercials.YTDLP:
            path = args[args.index("-o") + 1].replace("%(ext)s", "webm")
            pathlib.Path(path).write_bytes(b"raw")
        else:
            pathlib.Path(args[-1]).write_bytes(b"clip")
        return ""
    monkeypatch.setattr(commercials, "run", run)
    def encoder(path):
        return ["ffmpeg", "-i", path, "-f", "matroska", "-live", "1", "pipe:1"]
    async def check():
        path = await commercials.clip("g5WlcjXiZZk", encoder)
        assert path.read_bytes() == b"clip"
        assert await commercials.clip("g5WlcjXiZZk", encoder) == path
    asyncio.run(check())
    assert len(calls) == 2
    assert not old.exists()
    assert not list(tmp_path.glob("download-*"))
