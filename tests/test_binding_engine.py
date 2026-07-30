"""BindingEngine: user combo / hold-to-trigger bindings.

Same deal as test_combo_engine — the device layer needs hardware, but the
detection logic underneath it is pure and gets exercised here with an
injected clock.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cabinet"))
import inputs  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def engine(entries, clock=None):
    clock = clock or Clock()
    fired = []
    eng = inputs.BindingEngine(entries, lambda tgt, kind: fired.append((tgt, kind)),
                               now=clock)
    return eng, fired, clock


def test_tap_binding_fires_on_press():
    eng, fired, _ = engine([{"buttons": ["l"], "target": "start"}])
    eng.key_event("l", True)
    assert fired == [("start", "button")]


def test_tap_does_not_refire_while_held():
    eng, fired, _ = engine([{"buttons": ["l"], "target": "start"}])
    eng.key_event("l", True)
    eng.poll()
    eng.poll()
    assert fired == [("start", "button")]


def test_release_re_arms():
    eng, fired, _ = engine([{"buttons": ["l"], "target": "start"}])
    eng.key_event("l", True)
    eng.key_event("l", False)
    eng.key_event("l", True)
    assert fired == [("start", "button"), ("start", "button")]


def test_combo_needs_every_button():
    eng, fired, _ = engine([{"buttons": ["l", "r"], "target": "start"}])
    eng.key_event("l", True)
    assert fired == []
    eng.key_event("r", True)
    assert fired == [("start", "button")]


def test_hold_waits_for_the_duration():
    eng, fired, clock = engine(
        [{"buttons": ["select"], "hold_ms": 2000, "target": "screensaver"}])
    eng.key_event("select", True)
    assert fired == []
    clock.t = 1.9
    eng.poll()
    assert fired == []
    clock.t = 2.0
    eng.poll()
    assert fired == [("screensaver", "action")]


def test_hold_released_early_never_fires():
    eng, fired, clock = engine(
        [{"buttons": ["select"], "hold_ms": 2000, "target": "screensaver"}])
    eng.key_event("select", True)
    clock.t = 1.0
    eng.key_event("select", False)
    clock.t = 5.0
    eng.poll()
    assert fired == []


def test_longer_combo_suppresses_its_subset():
    """l+r -> start must not also fire the l -> a binding it contains."""
    eng, fired, _ = engine([
        {"buttons": ["l"], "target": "a"},
        {"buttons": ["l", "r"], "target": "start"},
    ])
    eng.key_event("l", True)
    assert fired == [("a", "button")]        # l alone is still l
    fired.clear()
    eng.key_event("l", False)
    eng.key_event("l", True)
    eng.key_event("r", True)
    assert ("start", "button") in fired
    assert fired.count(("a", "button")) == 1   # only the initial l-alone press


def test_subset_suppressed_when_pressed_together():
    eng, fired, _ = engine([
        {"buttons": ["l"], "target": "a"},
        {"buttons": ["l", "r"], "target": "start"},
    ])
    # both arrive before any poll — the combo wins outright
    eng.held = {"l": 0.0, "r": 0.0}
    eng.poll(0.0)
    assert fired == [("start", "button")]


def test_action_and_button_kinds():
    eng, fired, _ = engine([
        {"buttons": ["x"], "target": "save_state"},
        {"buttons": ["y"], "target": "b"},
    ])
    eng.key_event("x", True)
    eng.key_event("y", True)
    assert ("save_state", "action") in fired
    assert ("b", "button") in fired


def test_bad_entries_are_skipped_not_fatal():
    eng, fired, _ = engine([
        {"buttons": [], "target": "a"},                 # no buttons
        {"buttons": ["l"], "target": "nonsense"},        # unknown target
        {"buttons": ["r"], "hold_ms": -1, "target": "a"},  # negative hold
        "not even a mapping",
        {"buttons": ["z"], "target": "a"},               # the good one
    ])
    assert len(eng.errors) == 4
    assert len(eng.bindings) == 1
    eng.key_event("z", True)
    assert fired == [("a", "button")]


def test_string_button_spec_is_accepted():
    buttons, hold_s, target = inputs.parse_binding(
        {"buttons": "l+r", "hold_ms": 1500, "target": "start"})
    assert buttons == frozenset({"l", "r"})
    assert hold_s == 1.5
    assert target == "start"


def test_binding_expr_reads_like_the_ui_shows_it():
    assert inputs.binding_expr(frozenset({"l", "r"}), 2.0) == "l+r (hold 2s)"
    assert inputs.binding_expr(frozenset({"a"}), 0) == "a"


@pytest.mark.parametrize("target,kind", [
    ("a", "button"), ("start", "button"), ("screensaver", "action"),
    ("save_state", "action"), ("quit", "action"),
])
def test_target_kinds(target, kind):
    assert inputs.target_kind(target) == kind


def test_unknown_target_rejected():
    with pytest.raises(ValueError):
        inputs.target_kind("teleport")
