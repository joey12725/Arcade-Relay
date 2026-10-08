"""/controls/map keeps one claim per target in a layer, so the newest remap is
the one the cabinet's resolver (panel_layout.py) applies. Needs aiohttp +
pyyaml (the server's deps)."""
import asyncio
import importlib
import json
import pathlib
import sys

import pytest

pytest.importorskip("aiohttp")
yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "cabinet"))
import panel_layout as pl  # noqa: E402

LIVE = json.loads((ROOT / "tests/fixtures/live-controllers-2026-10-07.json")
                  .read_text())
NINTENDO = pl.NINTENDO


class Hub:
    def log_event(self, text):
        pass


class Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def ad(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    asyncio.set_event_loop(asyncio.new_event_loop())
    arcaded = importlib.import_module("arcaded")
    monkeypatch.setattr(arcaded, "STATE_DIR", str(tmp_path))
    commits = []
    monkeypatch.setattr(arcaded, "git_commit", commits.append)
    monkeypatch.setattr(arcaded, "push_state_soon", lambda: None)
    monkeypatch.setattr(arcaded, "hub", Hub())
    for rel, data in LIVE.items():
        arcaded._write_yaml(rel, data)
    return arcaded, commits


def remap(ad, scope, sets):
    resp = asyncio.run(ad.controls_map(Req({"scope": scope, "set": sets})))
    assert resp.status == 200, resp.text
    return json.loads(resp.text)


def layouts(ad, system="snes", rom=None):
    state = pathlib.Path(ad.STATE_DIR)
    bundle = {str(p.relative_to(state)): yaml.safe_load(p.read_text())
              for p in (state / "controllers").rglob("*.yaml")}
    return pl.effective_layouts(bundle, system, rom)


def without(layout, *numbers):
    return {n: t for n, t in layout.items() if n not in numbers}


def test_remap_onto_a_held_target_moves_it(ad):
    ad, commits = ad
    buttons = remap(ad, "global", {"player1.button2": "x"})["buttons"]
    assert "player1.button6" not in buttons
    assert (buttons["a"], buttons["player1.button5"]) == ("a", "b")
    assert layouts(ad) == {1: {**without(NINTENDO, 6), 2: "x"}, 2: NINTENDO}
    assert commits[-1].endswith("player1.button2=x (dropped player1.button6)")


def test_newest_remap_wins_whatever_the_panel_numbers(ad):
    ad, _ = ad
    remap(ad, "global", {"player1.button5": "x"})
    remap(ad, "global", {"player1.button2": "x"})
    # X left 5 (back to the B below it) for 2; 6 lost it on the first remap
    assert layouts(ad)[1] == {**without(NINTENDO, 6), 2: "x"}
    remap(ad, "global", {"player1.button6": "y"})   # X/Y swapped one at a time
    assert layouts(ad)[1] == {**NINTENDO, 2: "x", 6: "y"}


def test_a_both_player_key_beats_older_per_player_claims(ad):
    ad, _ = ad
    buttons = remap(ad, "global", {"button4": "l"})["buttons"]
    assert "player1.button3" not in buttons and "player1.button4" not in buttons
    for layout in layouts(ad).values():
        assert layout == {**without(NINTENDO, 3), 4: "l"}


def test_a_per_player_key_keeps_the_both_player_one(ad):
    ad, _ = ad
    scope, rom = "game:snes/smw.sfc", "/roms/snes/smw.sfc"
    remap(ad, scope, {"button6": "l2"})
    got = remap(ad, scope, {"player1.button2": "l2"})
    assert got["games"]["snes/smw.sfc"]["remap"] == {
        "button6": "l2", "player1.button2": "l2"}
    p1, p2 = layouts(ad, "snes", rom).values()
    assert p1 == {**without(NINTENDO, 6), 2: "l2"}
    assert p2 == {**NINTENDO, 6: "l2"}


def test_legacy_keys_merge_as_before(ad):
    ad, commits = ad
    got = remap(ad, "system:snes", {"a": "x", "player2.button1": "b"})
    assert got["remap"] == {"a": "x", "b": "a", "player2.button1": "b"}
    assert commits[-1] == ("arcadectl: controls map system:snes "
                           "a=x player2.button1=b")


def test_server_and_resolver_agree_on_panel_keys(ad):
    ad, _ = ad
    assert ad.PANEL_KEY.pattern == pl.ENTRY.pattern
