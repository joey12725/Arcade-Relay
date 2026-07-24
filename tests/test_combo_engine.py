#!/usr/bin/env python3
"""Unit tests for the production combo engine (pure logic, simulated clock).
Run: python3 tests/test_combo_engine.py"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cabinet"))

from inputs import ComboEngine, parse_combo  # noqa: E402

HOTKEYS = {
    "home_combo": "hold:start:2s",
    "screensaver_combo": "hold:select:2s",
    "lifeline_combo": "hold:start+select:5s",
}


def make(now_holder, fired):
    return ComboEngine(HOTKEYS, fired.append, now=lambda: now_holder[0])


def test_parse():
    assert parse_combo("hold:start:2s") == (frozenset({"start"}), 2.0)
    assert parse_combo("hold:start+select:5s") == (frozenset({"start", "select"}), 5.0)
    try:
        parse_combo("tap:start")
        raise AssertionError("bad spec accepted")
    except ValueError:
        pass


def test_hold_fires_once_and_latches():
    t, fired = [0.0], []
    e = make(t, fired)
    e.key_event("start", True)
    t[0] = 1.9; e.poll()
    assert fired == [], f"fired early: {fired}"
    t[0] = 2.1; e.poll()
    assert fired == ["home"], fired
    t[0] = 10.0; e.poll()
    assert fired == ["home"], f"re-fired while latched: {fired}"
    e.key_event("start", False)          # release un-latches
    e.key_event("start", True)
    t[0] = 12.5; e.poll()
    assert fired == ["home", "home"], fired


def test_release_before_hold_no_fire():
    t, fired = [0.0], []
    e = make(t, fired)
    e.key_event("start", True)
    t[0] = 1.5
    e.key_event("start", False)
    t[0] = 4.0; e.poll()
    assert fired == [], fired


def test_multibutton_lifeline():
    t, fired = [0.0], []
    e = make(t, fired)
    e.key_event("start", True)
    t[0] = 0.5; e.key_event("select", True)
    # 2s after start-down: home fires (start alone held 2s); select at 2s
    # also fires screensaver at 2.5. lifeline needs BOTH held 5s from the
    # LATER press => 5.5.
    t[0] = 5.4; e.poll()
    assert "lifeline" not in fired, fired
    t[0] = 5.6; e.poll()
    assert "lifeline" in fired, fired
    assert fired.count("lifeline") == 1


def test_partial_release_kills_multibutton():
    t, fired = [0.0], []
    e = make(t, fired)
    e.key_event("start", True)
    e.key_event("select", True)
    t[0] = 3.0
    e.key_event("select", False)          # broke the pair before 5s
    t[0] = 9.0; e.poll()
    assert "lifeline" not in fired, fired


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("COMBO-ENGINE TESTS PASS")
