"""Screensaver playlists: storage, selection, the resolved rotation, restarts,
the Home Assistant select entity and the cabinet's clip-cache key. Needs
aiohttp + pyyaml (the server's deps)."""
import asyncio
import importlib
import json
import pathlib
import sys
import urllib.error

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "cabinet"))

LIB = [{"id": "1", "name": "Anime A", "type": "Series"},
       {"id": "2", "name": "Anime B", "type": "Series"},
       {"id": "3", "name": "Elf", "type": "Movie"},
       {"id": "4", "name": "Spicy Show", "type": "Series"}]


class FakeHub:
    """Stands in for the cabinet link: records RPCs, answers ok."""

    def __init__(self, screensaver_on=False, fallback_plays=False):
        self.cab_state = {"screensaver": screensaver_on}
        self.online = True
        self.ws = None
        self.calls = []
        self.events = []
        self.fallback_plays = fallback_plays

    def log_event(self, text):
        self.events.append(text)

    async def rpc(self, method, params, timeout=None):
        self.calls.append((method, params))
        if method == "screensaver.on_fallback":
            return {"ok": True, "result": {"screensaver": self.fallback_plays,
                                           "note": "no cached clips"}}
        return {"ok": True, "result": {"screensaver": True}}


@pytest.fixture
def ad(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    # arcaded builds asyncio primitives at import; earlier asyncio.run()
    # calls in the suite leave the main thread without a current loop
    asyncio.set_event_loop(asyncio.new_event_loop())
    arcaded = importlib.import_module("arcaded")
    monkeypatch.setattr(arcaded, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(arcaded, "git_commit", lambda msg: None)
    monkeypatch.setattr(arcaded, "push_state_soon", lambda: None)
    monkeypatch.setattr(arcaded, "hub", FakeHub())
    # a fresh lock per test: each asyncio.run() is a new event loop
    monkeypatch.setattr(arcaded, "_restart_lock", asyncio.Lock())
    monkeypatch.setattr(arcaded, "_reported_missing_sources", set())

    async def items():
        return LIB

    async def episodes_of(sid):
        if sid not in {i["id"] for i in LIB}:
            raise urllib.error.HTTPError(f"/library/metadata/{sid}", 404,
                                         "Not Found", None, None)
        return [f"{sid}-e{n}" for n in range(2)]

    monkeypatch.setattr(arcaded.plex, "items", items)
    monkeypatch.setattr(arcaded.plex, "episodes_of", episodes_of)
    write(arcaded, {"shuffle": "in_order",
                    "sources": [{"plex_id": "1", "enabled": True},
                                {"plex_id": "2", "enabled": False}]})
    return arcaded


def write(ad, cfg):
    ad._write_yaml(ad.SCREENSAVER_REL, cfg)


class Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def call(fn, body=None):
    resp = asyncio.run(fn(Req(body)))
    return resp.status, json.loads(resp.text)


def save(ad, body):
    return call(ad.screensaver_playlists_save, body)


def create(ad, name, sources=()):
    return save(ad, {"name": name, "create": True, "sources": list(sources)})


def select(ad, name):
    return call(ad.screensaver_playlists_select, {"name": name})


def rotation(ad):
    return json.loads(asyncio.run(ad.screensaver_playlist(None)).text)


def rotation_sources(ad):
    return [p.split("/media/")[1].split("-")[0] for p in rotation(ad)["playlist"]]


def test_default_rotation_is_enabled_sources_only(ad):
    r = rotation(ad)
    assert r["active_playlist"] is None
    assert r["playlist"] == ["/media/1-e0", "/media/1-e1"]


def test_playlist_plays_exactly_its_members_even_if_off_on_the_board(ad):
    assert create(ad, "Christmas", ["3", "2"])[0] == 201
    assert select(ad, "christmas")[0] == 200
    assert rotation(ad)["active_playlist"] == "Christmas"
    assert rotation_sources(ad) == ["3", "3", "2", "2"]
    assert select(ad, "all")[0] == 200
    assert rotation_sources(ad) == ["1", "1"]


def test_nsfw_title_off_the_board_only_plays_in_its_playlist(ad):
    create(ad, "nsfw", ["4"])
    assert "4" not in rotation_sources(ad)
    select(ad, "NSFW")
    assert set(rotation_sources(ad)) == {"4"}
    select(ad, None)
    assert "4" not in rotation_sources(ad)


def test_add_remove_dedupe_and_unknown_names(ad):
    create(ad, "sfw")
    _, v = save(ad, {"name": "SFW", "add": ["1", "2", "1"]})
    assert [s["id"] for s in v["playlists"][0]["sources"]] == ["1", "2"]
    _, v = save(ad, {"name": "sfw", "remove": ["1"]})
    assert [s["id"] for s in v["playlists"][0]["sources"]] == ["2"]
    assert len(v["playlists"]) == 1
    # add/remove on a name that doesn't exist is a typo, not a create
    assert save(ad, {"name": "sfww", "add": ["1"]})[0] == 404
    assert save(ad, {"name": "sfww", "sources": ["1"]})[0] == 404
    assert select(ad, "nope")[0] == 404


def test_create_never_overwrites_an_existing_playlist(ad):
    create(ad, "Christmas", ["1", "3"])
    status, out = create(ad, "christmas")
    assert status == 409 and out["existing"] == "Christmas"
    assert ad.load_screensaver()["playlists"]["Christmas"]["sources"] == ["1", "3"]


def test_reserved_and_invalid_input(ad):
    for bad in ("all", "Default", "all shows", "None", "unknown",
                "unavailable", "", "x" * 41):
        assert create(ad, bad)[0] == 400, bad
    assert save(ad, {"name": "ok", "create": True, "sources": "1"})[0] == 400
    with pytest.raises(ad.web.HTTPBadRequest):
        call(ad.screensaver_playlists_save, ["not", "an", "object"])


def test_deleting_the_active_playlist_returns_to_default(ad):
    create(ad, "halloween", ["3"])
    select(ad, "halloween")
    assert call(ad.screensaver_playlists_delete, {"name": "halloween"})[0] == 200
    assert ad.load_screensaver()["active_playlist"] is None
    assert call(ad.screensaver_playlists_delete, {"name": "halloween"})[0] == 404


def test_empty_playlist_warns_by_fallback(ad):
    create(ad, "empty")
    status, out = select(ad, "empty")
    assert status == 200 and "nothing will play" in out["warning"]
    select(ad, "all")
    cfg = ad.load_screensaver()
    cfg["fallback"] = "dim_clock"
    write(ad, cfg)
    assert "dim clock" in select(ad, "empty")[1]["warning"]


def test_hand_edited_shapes_are_normalized_not_fatal(ad):
    for raw in (None, ["a", "b"], "junk"):
        write(ad, {"playlists": raw, "sources": []})
        assert call(ad.screensaver_playlists_get)[0] == 200
        assert select(ad, "all")[0] == 200
    write(ad, {"shuffle": "in_order", "sources": [],
               "playlists": {"xmas": [3, 1], 2024: None, "bad": {"sources": None}},
               "active_playlist": "XMAS"})
    cfg = ad.load_screensaver()
    assert cfg["playlists"] == {"xmas": {"sources": ["3", "1"]},
                                "2024": {"sources": []},
                                "bad": {"sources": []}}
    assert rotation_sources(ad) == ["3", "3", "1", "1"]
    _, v = save(ad, {"name": "xmas", "remove": ["3"], "add": [1]})
    assert [s["id"] for s in v["playlists"][2]["sources"]] == ["1"]


def test_unknown_member_is_skipped_and_warned(ad):
    status, out = create(ad, "mixed", ["3", "999"])
    assert status == 201 and "999" in out["warning"]
    select(ad, "mixed")
    assert rotation_sources(ad) == ["3", "3"]
    assert any("999" in e for e in ad.hub.events)


def test_switch_restarts_a_running_screensaver_on_the_new_list(ad, monkeypatch):
    monkeypatch.setattr(ad, "hub", FakeHub(screensaver_on=True))
    create(ad, "xmas", ["3"])
    select(ad, "xmas")
    method, params = ad.hub.calls[-1]
    assert method == "screensaver.on"
    assert params["active_playlist"] == "xmas"
    assert params["playlist"] == ["/media/3-e0", "/media/3-e1"]


def test_switch_to_empty_stops_the_old_list_instead_of_leaving_it(ad, monkeypatch):
    monkeypatch.setattr(ad, "hub", FakeHub(screensaver_on=True))
    create(ad, "empty")
    status, out = select(ad, "empty")
    assert status == 200
    assert [m for m, _ in ad.hub.calls] == ["screensaver.on_fallback", "screensaver.off"]
    assert "stopped" in out["warning"]


def test_restart_failure_is_a_warning_not_an_error(ad, monkeypatch):
    monkeypatch.setattr(ad, "hub", FakeHub(screensaver_on=True))
    create(ad, "xmas", ["3"])

    async def boom():
        raise ConnectionResetError("plex went away")
    monkeypatch.setattr(ad.plex, "items", boom)
    status, out = select(ad, "xmas")
    assert status == 200 and "restarting the screensaver failed" in out["warning"]
    assert ad.load_screensaver()["active_playlist"] == "xmas"


def test_back_to_back_switches_end_on_the_last_choice(ad, monkeypatch):
    monkeypatch.setattr(ad, "hub", FakeHub(screensaver_on=True))
    create(ad, "slow", ["1"])
    create(ad, "fast", ["3"])
    real = ad.plex.episodes_of

    async def episodes_of(sid):
        await asyncio.sleep(0.05 if sid == "1" else 0)
        return await real(sid)
    monkeypatch.setattr(ad.plex, "episodes_of", episodes_of)

    async def both():
        ad._restart_lock = asyncio.Lock()
        await asyncio.gather(ad.select_playlist("slow"), ad.select_playlist("fast"))
    asyncio.run(both())
    ons = [p for m, p in ad.hub.calls if m == "screensaver.on"]
    assert ons[-1]["active_playlist"] == "fast"


def test_home_assistant_command_selects(ad):
    create(ad, "xmas", ["3"])
    asyncio.run(ad._integration_command("playlist", "XMAS", "set"))
    assert ad.load_screensaver()["active_playlist"] == "xmas"
    asyncio.run(ad._integration_command("playlist", "All shows", "set"))
    assert ad.load_screensaver()["active_playlist"] is None


def test_home_assistant_select_entity_and_state():
    import integrations
    active, options = integrations.playlist_fields(
        {"playlists": {"halloween": {}, "Christmas": {}}, "active_playlist": "CHRISTMAS"})
    assert active == "Christmas"
    assert options == ["All shows", "Christmas", "halloween"]
    assert integrations.playlist_fields({})[0] == "All shows"
    assert integrations.playlist_fields(None) == ("All shows", ["All shows"])
    # hand-edited junk degrades instead of stopping all telemetry
    assert integrations.playlist_fields(
        {"playlists": {2024: [1], "x": None}})[1] == ["All shows", "2024", "x"]

    pub = integrations.HomeAssistantPublisher({}, lambda _: None)
    pub.playlist_options = options
    (topic, payload), = [e for e in pub._discovery() if "/select/" in e[0]]
    assert payload["options"] == options
    assert payload["command_topic"].endswith("playlist/set")


def test_publish_reannounces_when_playlists_change():
    import integrations
    published = []

    class Client:
        def is_connected(self):
            return True

        def publish(self, topic, payload, **kw):
            published.append((topic, json.loads(payload)))

    pub = integrations.HomeAssistantPublisher({}, lambda _: None)
    pub.client = Client()
    snap = integrations.snapshot_from({}, True, screensaver_cfg={
        "playlists": {"xmas": {"sources": ["3"]}}, "active_playlist": "xmas"})
    pub.publish(snap)
    select_cfg = [p for t, p in published if "/select/" in t]
    assert select_cfg[-1]["options"] == ["All shows", "xmas"]
    state = [p for t, p in published if t.endswith("/state")][-1]
    assert state["playlist"] == "xmas"

    published.clear()
    pub.publish(snap)
    assert not [t for t, _ in published if "/select/" in t]   # unchanged: no re-announce
    snap2 = integrations.snapshot_from({}, True, screensaver_cfg={
        "playlists": {"xmas": {}, "halloween": {}}})
    pub.publish(snap2)
    assert [p for t, p in published if "/select/" in t][-1]["options"] == \
        ["All shows", "halloween", "xmas"]


def test_ha_playlist_command_routes_to_select(monkeypatch):
    import ha_local
    got = []
    real_sleep = asyncio.sleep

    async def run():
        async def select_pl(name):
            got.append(name)
        calls = []

        class Pub:
            def __init__(self, cfg, log, on_command):
                self.on_command = on_command
                self.enabled = False
                self.client = None
                calls.append(self)

            def disconnect(self):
                pass

        async def stop(_):
            raise asyncio.CancelledError()
        monkeypatch.setattr(ha_local.integrations, "HomeAssistantPublisher", Pub)
        monkeypatch.setattr(ha_local.asyncio, "sleep", stop)
        try:
            await ha_local.run(lambda: {}, lambda: {}, lambda: {}, None,
                               lambda *_: None, select_playlist=select_pl)
        except asyncio.CancelledError:
            pass
        calls[0].on_command("playlist", "Christmas", "set")
        await real_sleep(0)
        await real_sleep(0)
    asyncio.run(run())
    assert got == ["Christmas"]


def test_cabinet_clip_cache_key_and_clear(tmp_path, monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    key = cabd.playlist_cache_key
    assert key({}) is None
    assert key({"screensaver.yaml": None}) is None
    base = {"playlists": {"Xmas": {"sources": ["3", "1"]}}}
    assert key({"screensaver.yaml": base}) is None          # default rotation
    on = dict(base, active_playlist="xmas")
    assert key({"screensaver.yaml": on}) == ("xmas", ("1", "3"))
    edited = {"playlists": {"Xmas": {"sources": ["3"]}}, "active_playlist": "Xmas"}
    assert key({"screensaver.yaml": edited}) != key({"screensaver.yaml": on})
    assert key({"screensaver.yaml": {"active_playlist": "gone"}}) is None

    monkeypatch.setattr(cabd, "CLIPS_DIR", str(tmp_path))
    for f in ("clip-0.ts", "clip-1.ts.part", "keep.txt"):
        (tmp_path / f).write_text("x")
    gen = cabd.clips_gen["n"]
    cabd.clear_clip_cache()
    assert cabd.clips_gen["n"] == gen + 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_one_resolver_handles_hand_edited_names():
    import integrations
    cfg = {"playlists": {" Xmas ": {"sources": 4821}, 0: ["1"], "": ["2"]},
           "active_playlist": " xmas"}
    assert integrations.normalize_playlists(cfg["playlists"]) == {
        "Xmas": {"sources": ["4821"]}, "0": {"sources": ["1"]}}
    assert integrations.resolve_active(cfg) == "Xmas"
    assert integrations.resolve_active(dict(cfg, active_playlist=0)) == "0"
    assert integrations.resolve_active(dict(cfg, active_playlist="gone")) is None
    assert integrations.resolve_active(dict(cfg, active_playlist="")) is None
    assert integrations.playlist_fields(cfg)[0] == "Xmas"


def test_noop_edit_makes_no_commit(ad, monkeypatch):
    create(ad, "xmas", ["3"])
    commits = []
    monkeypatch.setattr(ad, "git_commit", commits.append)
    assert save(ad, {"name": "xmas", "add": ["3"]})[0] == 200
    assert save(ad, {"name": "xmas", "remove": ["999"]})[0] == 200
    assert commits == []
    save(ad, {"name": "xmas", "add": ["1"]})
    assert commits == ["arcadectl: screensaver playlist edit xmas"]


def test_missing_source_is_reported_once(ad):
    create(ad, "stale", ["999", "3"])
    select(ad, "stale")
    for _ in range(3):
        rotation(ad)
    hits = [e for e in ad.hub.events if "999" in e and "skipped" in e]
    assert len(hits) == 1 and ad._severity(hits[0]) != "FAILED"


def test_stale_only_playlist_hint_is_not_called_empty(ad, monkeypatch):
    monkeypatch.setattr(ad, "hub", FakeHub(screensaver_on=True))
    create(ad, "stale", ["999"])
    cfg = ad.load_screensaver()
    cfg["active_playlist"] = "stale"
    write(ad, cfg)
    res, err = asyncio.run(ad.screensaver_start())
    msg = json.loads(err.text)["error"]
    assert "nothing Plex can play" in msg and "is empty" not in msg


def test_restart_reason_never_reads_none(ad, monkeypatch):
    hub = FakeHub(screensaver_on=True)

    async def rpc(method, params, timeout=None):
        hub.calls.append((method, params))
        return {"ok": True, "result": {"screensaver": False}}
    hub.rpc = rpc
    monkeypatch.setattr(ad, "hub", hub)
    create(ad, "xmas", ["3"])
    out = select(ad, "xmas")[1]
    assert "None" not in out["warning"]
    assert "could not start" in out["warning"]


def test_cabinet_rechecks_a_screensaver_started_on_the_old_playlist(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    started = []

    async def fake_start(only_if_on=False):
        started.append(True)
        cabd.ss["playlist"] = cabd.active_playlist_name(cabd.state["bundle"])
    monkeypatch.setattr(cabd, "start_screensaver_now", fake_start)
    monkeypatch.setitem(cabd.state, "bundle", {"screensaver.yaml": {
        "playlists": {"Xmas": ["3"]}, "active_playlist": "xmas"}})
    monkeypatch.setitem(cabd.ss, "on", True)
    monkeypatch.setitem(cabd.ss, "starting", False)
    monkeypatch.setitem(cabd.ss, "playlist", None)       # started on default
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert started == [True]
    started.clear()
    monkeypatch.setitem(cabd.ss, "playlist", "Xmas")     # server already restarted it
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert started == []
    monkeypatch.setitem(cabd.ss, "on", False)
    monkeypatch.setitem(cabd.ss, "playlist", None)
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert started == []


def test_transient_plex_error_still_fails_and_is_not_marked_missing(ad, monkeypatch):
    create(ad, "xmas", ["3"])
    select(ad, "xmas")

    async def flaky(sid):
        raise TimeoutError("plex slow")
    monkeypatch.setattr(ad.plex, "episodes_of", flaky)
    with pytest.raises(TimeoutError):
        rotation(ad)
    assert "3" not in ad._reported_missing_sources


def test_missing_report_rearms_once_the_source_works_again(ad, monkeypatch):
    create(ad, "xmas", ["3"])
    select(ad, "xmas")
    real = ad.plex.episodes_of
    gone = {"on": True}

    async def episodes_of(sid):
        if gone["on"]:
            raise urllib.error.HTTPError("x", 404, "Not Found", None, None)
        return await real(sid)
    monkeypatch.setattr(ad.plex, "episodes_of", episodes_of)
    rotation(ad)
    gone["on"] = False
    rotation(ad)
    gone["on"] = True
    rotation(ad)
    assert len([e for e in ad.hub.events if "skipped source 3" in e]) == 2


def test_duplicate_names_merge_and_active_is_canonical(ad, monkeypatch):
    write(ad, {"sources": [], "playlists": {"Xmas": ["3"], " xmas ": ["1", "3"]},
               "active_playlist": " XMAS "})
    cfg = ad.load_screensaver()
    (name, entry), = cfg["playlists"].items()           # one playlist, merged
    assert name.lower() == "xmas" and sorted(entry["sources"]) == ["1", "3"]
    assert cfg["active_playlist"] == name
    write(ad, {"sources": [], "playlists": {}, "active_playlist": "deleted"})
    assert ad.load_screensaver()["active_playlist"] is None
    create(ad, "Xmas", ["3"])
    select(ad, "Xmas")
    commits = []
    monkeypatch.setattr(ad, "git_commit", commits.append)
    select(ad, "xMAS")                       # already active: no commit
    assert commits == []


def test_restart_is_skipped_if_the_screensaver_went_off_meanwhile(ad, monkeypatch):
    hub = FakeHub(screensaver_on=True)

    async def rpc(method, params, timeout=None):
        hub.calls.append((method, params))
        return {"ok": True, "result": {"screensaver": False, "note": "not running"}}
    hub.rpc = rpc
    monkeypatch.setattr(ad, "hub", hub)
    create(ad, "xmas", ["3"])
    out = select(ad, "xmas")[1]
    assert [m for m, _ in hub.calls] == ["screensaver.on"]
    assert hub.calls[0][1]["only_if_on"] is True
    assert "warning" not in out


def _cabd_screen(monkeypatch, cabd, started):
    def start(playlist, brightness, timeout, **kw):
        started.append(list(playlist))
        return True
    monkeypatch.setattr(cabd.screensaver, "start", start)
    monkeypatch.setitem(cabd.game, "session", None)
    monkeypatch.setitem(cabd.ll, "on", False)
    monkeypatch.setitem(cabd.ss, "live", {})
    monkeypatch.setitem(cabd.ss, "suppress_until", 0.0)


def test_cabinet_restart_respects_off_and_records_only_successes(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    started = []
    _cabd_screen(monkeypatch, cabd, started)

    async def go():
        monkeypatch.setattr(cabd, "game_lock", asyncio.Lock())
        cabd.ss["on"], cabd.ss["playlist"] = False, "old"
        r = await cabd.screensaver_on(["/media/1"], 80, playlist_name="Xmas",
                                      only_if_on=True)
        assert r == {"screensaver": False, "note": "not running"}
        assert started == [] and cabd.ss["playlist"] == "old"
        cabd.ss["on"] = True
        cabd.ss["suppress_until"] = cabd.time.monotonic() + 600
        r = await cabd.screensaver_on(["/media/1"], 80, playlist_name="Xmas",
                                      only_if_on=True)
        assert r["note"] == "not running"
        cabd.ss["suppress_until"] = 0.0
        r = await cabd.screensaver_on(["/media/1"], 80, playlist_name="Xmas",
                                      only_if_on=True)
        assert r == {"screensaver": True} and cabd.ss["playlist"] == "Xmas"
    asyncio.run(go())


def test_cabinet_recheck_stops_the_old_list_when_the_new_one_gives_nothing(monkeypatch):
    asyncio.set_event_loop(asyncio.new_event_loop())
    cabd = importlib.import_module("cabd")
    calls = []

    async def nothing_started(only_if_on=False):
        calls.append(("start", only_if_on))

    async def off():
        calls.append(("off",))
        cabd.ss["on"] = False
    monkeypatch.setattr(cabd, "start_screensaver_now", nothing_started)
    monkeypatch.setattr(cabd, "screensaver_off", off)
    monkeypatch.setitem(cabd.state, "bundle", {"screensaver.yaml": {
        "playlists": {"Empty": []}, "active_playlist": "Empty"}})
    monkeypatch.setitem(cabd.ss, "on", True)
    monkeypatch.setitem(cabd.ss, "starting", False)
    monkeypatch.setitem(cabd.ss, "playlist", "nsfw")
    asyncio.run(cabd.recheck_screensaver_playlist(settle_s=0))
    assert calls == [("start", True), ("off",)]


def test_brightness_default_and_range(ad):
    assert ad.load_screensaver()["brightness"] == 100
    for value in (0, 100, 150, 200):
        response = call(ad.screensaver_set, {"brightness": value})
        assert response[0] == 200
        assert ad.load_screensaver()["brightness"] == value
    for value in (-1, 201, True, "100", 100.5, None):
        response = call(ad.screensaver_set, {"brightness": value})
        assert response[0] == 400
        assert ad.load_screensaver()["brightness"] == 200
