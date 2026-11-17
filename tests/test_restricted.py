"""The screensaver's restricted (NSFW) filter: the Plex `restricted` label
applied over whatever is selected — ignored (default), hidden, or the only
thing played — from the dashboard/CLI endpoint, Home Assistant and the
cabinet's clip cache. Needs aiohttp + pyyaml (the server's deps)."""
import asyncio
import importlib
import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("yaml")

import test_playlists  # noqa: E402
from test_playlists import (FakeHub, call, create, rotation,  # noqa: E402
                            rotation_sources, select, write)

ad = test_playlists.ad      # the server fixture, shared

import integrations  # noqa: E402

# "4" (Spicy Show) carries the restricted label, as in the live library
RESTRICTED_LIB = [{"id": "1", "name": "Anime A", "type": "Series", "restricted": False},
                  {"id": "2", "name": "Anime B", "type": "Series", "restricted": False},
                  {"id": "3", "name": "Elf", "type": "Movie", "restricted": False},
                  {"id": "4", "name": "Spicy Show", "type": "Series", "restricted": True}]


@pytest.fixture
def rad(ad, monkeypatch):
    async def items():
        return RESTRICTED_LIB
    monkeypatch.setattr(ad.plex, "items", items)
    write(ad, {"shuffle": "in_order",
               "sources": [{"plex_id": "1", "enabled": True},
                           {"plex_id": "4", "enabled": True}]})
    return ad


def set_mode(server, mode):
    return call(server.screensaver_restricted_set, {"mode": mode})


def test_default_is_any_and_plays_the_selection_as_is(rad):
    r = rotation(rad)
    assert r["restricted"] == "any"
    assert rotation_sources(rad) == ["1", "1", "4", "4"]


def test_hide_and_only_over_the_board(rad):
    assert set_mode(rad, "hide")[0] == 200
    assert set(rotation_sources(rad)) == {"1"}
    assert set_mode(rad, "only")[0] == 200
    assert set(rotation_sources(rad)) == {"4"}
    assert set_mode(rad, "any")[0] == 200
    assert set(rotation_sources(rad)) == {"1", "4"}


def test_filter_applies_inside_a_playlist_too(rad):
    create(rad, "late", ["2", "4"])
    select(rad, "late")
    set_mode(rad, "only")
    assert set(rotation_sources(rad)) == {"4"}
    set_mode(rad, "hide")
    assert set(rotation_sources(rad)) == {"2"}


def test_unknown_ids_are_dropped_only_when_filtering(rad, monkeypatch):
    # "9" is not in the library listing: its label can't be checked
    write(rad, {"shuffle": "in_order",
                "sources": [{"plex_id": "1", "enabled": True},
                            {"plex_id": "9", "enabled": True}]})

    async def episodes_of(sid):
        return [f"{sid}-e0"]
    monkeypatch.setattr(rad.plex, "episodes_of", episodes_of)
    assert rotation_sources(rad) == ["1", "9"]
    set_mode(rad, "hide")
    assert rotation_sources(rad) == ["1"]
    set_mode(rad, "only")
    assert rotation_sources(rad) == []


def test_aliases_labels_and_bad_values(rad):
    for value, mode in (("SFW", "hide"), ("nsfw", "only"), ("Hide NSFW", "hide"),
                        ("NSFW only", "only"), ("Ignore tag", "any"),
                        ("  default ", "any")):
        status, out = set_mode(rad, value)
        assert (status, out["restricted"]) == (200, mode), value
    for bad in ("maybe", "", None, True, 3):
        status, out = set_mode(rad, bad)
        assert status == 400 and "any, hide, only" in out["error"]
    assert call(rad.screensaver_restricted_get)[1]["restricted"] == "any"


def test_hand_edited_value_falls_back_to_any(rad):
    write(rad, {"restricted": True, "sources": [{"plex_id": "4", "enabled": True}]})
    assert rad.load_screensaver()["restricted"] == "any"
    write(rad, {"restricted": "Hide", "sources": [{"plex_id": "4", "enabled": True}]})
    assert rad.load_screensaver()["restricted"] == "hide"


def test_change_commits_and_restarts_a_running_screensaver(rad, monkeypatch):
    commits = []
    monkeypatch.setattr(rad, "git_commit", commits.append)
    monkeypatch.setattr(rad, "hub", FakeHub(screensaver_on=True))
    set_mode(rad, "hide")
    assert commits == ["arcadectl: screensaver restricted hide"]
    (method, params), = rad.hub.calls
    assert method == "screensaver.on" and params["only_if_on"]
    assert params["restricted"] == "hide"
    assert {p.split("/media/")[1].split("-")[0] for p in params["playlist"]} == {"1"}
    # same value again: no commit, no restart
    rad.hub.calls.clear()
    set_mode(rad, "sfw")
    assert commits == ["arcadectl: screensaver restricted hide"]
    assert rad.hub.calls == []


def test_only_with_nothing_tagged_stops_and_says_why(rad, monkeypatch):
    write(rad, {"sources": [{"plex_id": "1", "enabled": True}]})
    monkeypatch.setattr(rad, "hub", FakeHub(screensaver_on=True))
    status, out = set_mode(rad, "only")
    assert status == 200
    assert "nothing in the rotation is tagged restricted" in out["warning"]
    assert ("screensaver.off", {}) in rad.hub.calls


def test_home_assistant_command_sets_the_filter(rad):
    asyncio.run(rad._integration_command("restricted", "NSFW only", "set"))
    assert rad.load_screensaver()["restricted"] == "only"
    asyncio.run(rad._integration_command("restricted", "bogus", "set"))
    assert rad.load_screensaver()["restricted"] == "only"
    assert any("bogus" in e for e in rad.hub.events)


def test_home_assistant_select_entity_and_state():
    pub = integrations.HomeAssistantPublisher({}, lambda _: None)
    (topic, payload), = [e for e in pub._discovery()
                         if "/select/arcade_cabinet/restricted/" in e[0]]
    assert payload["options"] == ["Ignore tag", "Hide NSFW", "NSFW only"]
    assert payload["command_topic"] == "arcade/arcade_cabinet/restricted/set"
    assert payload["value_template"] == "{{ value_json.restricted }}"
    snap = integrations.snapshot_from({}, True, screensaver_cfg={"restricted": "hide"})
    assert snap.as_dict()["restricted"] == "Hide NSFW"
    assert integrations.snapshot_from({}, True).restricted == "Ignore tag"


def test_mqtt_message_routes_as_restricted():
    got = []
    pub = integrations.HomeAssistantPublisher({}, lambda _: None,
                                              lambda *a: got.append(a))

    class Msg:
        topic = "arcade/arcade_cabinet/restricted/set"
        payload = b"Hide NSFW"
        retain = False
    pub._on_message(None, None, Msg())
    assert got == [("restricted", "Hide NSFW", "set")]


def test_cabinet_ha_routes_restricted_to_the_server(monkeypatch):
    import ha_local
    got = []
    real_sleep = asyncio.sleep

    async def run():
        async def set_restricted(value):
            got.append(value)
        pubs = []

        class Pub:
            def __init__(self, cfg, log, on_command):
                self.on_command = on_command
                self.enabled = False
                self.client = None
                pubs.append(self)

            def disconnect(self):
                pass

        async def stop(_):
            raise asyncio.CancelledError()
        monkeypatch.setattr(ha_local.integrations, "HomeAssistantPublisher", Pub)
        monkeypatch.setattr(ha_local.asyncio, "sleep", stop)
        try:
            await ha_local.run(lambda: {}, lambda: {}, lambda: {}, None,
                               lambda *_: None, set_restricted=set_restricted)
        except asyncio.CancelledError:
            pass
        pubs[0].on_command("restricted", "NSFW only", "set")
        await real_sleep(0)
        await real_sleep(0)
    asyncio.run(run())
    assert got == ["NSFW only"]


def test_cabinet_clip_cache_key_tracks_the_filter():
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    key = cabd.playlist_cache_key
    # default rotation, filter off: unchanged from before the filter existed
    assert key({"screensaver.yaml": {}}) is None
    assert key({"screensaver.yaml": {"restricted": "any"}}) is None
    hide = key({"screensaver.yaml": {"restricted": "hide"}})
    only = key({"screensaver.yaml": {"restricted": "only"}})
    assert None not in (hide, only) and hide != only
    pl = {"playlists": {"Xmas": ["3"]}, "active_playlist": "xmas"}
    assert key({"screensaver.yaml": pl}) != key(
        {"screensaver.yaml": {**pl, "restricted": "hide"}})


def test_cabinet_rechecks_a_screensaver_started_under_the_old_filter(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    started = []

    async def fake_start(only_if_on=False):
        started.append(True)
        cabd.ss["restricted"] = cabd.restricted_mode(cabd.state["bundle"])
    monkeypatch.setattr(cabd, "start_screensaver_now", fake_start)
    monkeypatch.setitem(cabd.state, "bundle", {"screensaver.yaml": {"restricted": "hide"}})
    monkeypatch.setitem(cabd.ss, "on", True)
    monkeypatch.setitem(cabd.ss, "starting", False)
    monkeypatch.setitem(cabd.ss, "playlist", None)
    monkeypatch.setitem(cabd.ss, "restricted", "any")    # started before the switch
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert started == [True]
    started.clear()
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert started == []


def test_plex_restricted_keys_come_from_the_label_directory(monkeypatch):
    plex = importlib.import_module("plex")
    seen = []

    def fake_req(path, method="GET", timeout=30):
        seen.append(path)
        if path.startswith("/library/sections/2/label"):
            return ET.fromstring('<MediaContainer><Directory key="140993" title="Restricted"/>'
                                 '<Directory key="7" title="kids"/></MediaContainer>')
        if "label=140993" in path:
            return ET.fromstring('<MediaContainer><Directory ratingKey="44"/>'
                                 '<Directory ratingKey="45"/></MediaContainer>')
        raise AssertionError(path)
    monkeypatch.setattr(plex, "_req", fake_req)
    monkeypatch.setattr(plex, "PLEX_TOKEN", None)
    assert plex._restricted_keys_sync("2") == {"44", "45"}
    assert not any("label=7" in p for p in seen)
