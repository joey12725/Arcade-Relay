"""The server validates combo bindings; the cabinet executes them.

They keep separate copies of the target vocabulary because the server image
does not ship the cabinet package. That is fine right up until someone adds a
target to one and not the other, at which point the UI offers a binding cabd
silently ignores. This test is the thing that stops that.
"""
import os
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "cabinet"))
sys.path.insert(0, os.path.join(ROOT, "server"))

import inputs  # noqa: E402


def _server_targets():
    """Pull the tuples out of arcaded without importing it — importing drags
    in aiohttp, plex and the whole daemon for two constants."""
    import ast
    src = open(os.path.join(ROOT, "server", "arcaded.py")).read()
    tree = ast.parse(src)
    found = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id in ("PAD_TARGETS",
                                                        "ACTION_TARGETS"):
                found[tgt.id] = ast.literal_eval(node.value)
    return found


def test_pad_targets_match():
    assert _server_targets()["PAD_TARGETS"] == inputs.PAD_TARGETS


def test_action_targets_match():
    assert _server_targets()["ACTION_TARGETS"] == inputs.ACTION_TARGETS


def test_every_pad_target_has_a_uinput_code():
    """A pad target with no uinput mapping would validate, save, fire — and
    then do nothing, because VirtualPad.tap() bails on an unknown button."""
    missing = [t for t in inputs.PAD_TARGETS
               if t not in inputs.VirtualPad.UINPUT_CODES]
    assert missing == []


def test_every_action_target_is_dispatchable():
    """Each action must be handled by cabd.on_action: either a cabinet combo,
    the quit special case, or a RetroArch network command."""
    import re
    src = open(os.path.join(ROOT, "cabinet", "cabd.py")).read()
    retro = set(re.findall(r'"(\w+)":\s*"[A-Z_]+"',
                           src.split("RETRO_COMMANDS = {")[1].split("}")[0]))
    combos = set(re.search(r'LOGICAL_COMBOS = \(([^)]*)\)', src).group(1)
                 .replace('"', "").replace(" ", "").strip(",").split(","))
    handled = retro | combos | {"quit"}
    assert set(inputs.ACTION_TARGETS) - handled == set()


@pytest.mark.parametrize("bad", [
    {"buttons": [], "target": "a"},
    {"buttons": ["l"], "target": "nope"},
    {"buttons": ["l", "l"], "target": "a"},
    {"buttons": ["l"], "hold_ms": -5, "target": "a"},
    {"buttons": ["l"], "hold_ms": "soon", "target": "a"},
])
def test_server_rejects_bad_bindings(bad):
    """_norm_binding is the server's gate; pull it out the same way."""
    src = open(os.path.join(ROOT, "server", "arcaded.py")).read()
    mod = {}
    start = src.index("PAD_TARGETS = (")
    end = src.index("def _same_expr(")
    exec(compile(src[start:end], "arcaded_subset", "exec"), mod)  # noqa: S102
    with pytest.raises(ValueError):
        mod["_norm_binding"](bad)


def test_server_normalises_binding_form():
    src = open(os.path.join(ROOT, "server", "arcaded.py")).read()
    mod = {}
    start = src.index("PAD_TARGETS = (")
    end = src.index("def _same_expr(")
    exec(compile(src[start:end], "arcaded_subset", "exec"), mod)  # noqa: S102
    got = mod["_norm_binding"]({"buttons": "R+L", "hold_ms": "2000",
                                "target": "START"})
    assert got == {"buttons": ["l", "r"], "hold_ms": 2000, "target": "start"}
    # and the cabinet parses what the server stored
    buttons, hold_s, target = inputs.parse_binding(got)
    assert buttons == frozenset({"l", "r"}) and hold_s == 2.0 and target == "start"
