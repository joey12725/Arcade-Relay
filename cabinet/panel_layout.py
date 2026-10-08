#!/usr/bin/env python3
"""Which RetroPad button each numbered panel button is, per player.

Panels: top row 1 2 3 4, bottom row 5 6 7 8. Layers, lowest first: NINTENDO
base; global `buttons`; SYSTEM_LAYOUTS (cores not lettered like an SNES pad,
so a global remap in SNES letters cannot scramble them); system `remap`; game
`remap`. User entries are `player<P>.button<N>: <target>` or `button<N>:
<target>` (both players); other keys (the sim's logical `a: b`) never touch
the panels. renderer.py writes the result as both pads' RetroArch autoconfig.
"""
import json
import re
from pathlib import Path

PROFILE = Path(__file__).parent / "provision/controllers/panels.json"
VIRTUAL_VENDOR = 0xf055   # panels.py VENDOR: the calibrated virtual pads
BASE_CODE = 304          # virtual-pad evdev code that RetroArch indexes as 0
TARGETS = ("a", "b", "x", "y", "l", "r", "l2", "r2", "start", "select")
PLAYERS = (1, 2)
PHYSICAL = re.compile(r"(?:player\d+\.)?button\d+")
ENTRY = re.compile(r"(?:player([12])\.)?button([1-8])")

# NES, SNES, GB, GBA, PSX: the cores take RetroPad letters as SNES letters
# (PSX by SNES position: B Cross, A Circle, Y Square, X Triangle).
NINTENDO = {1: "a", 2: "y", 3: "l", 4: "r", 5: "b", 6: "x",
            7: "start", 8: "select"}

SYSTEM_LAYOUTS = {
    # Genesis Plus GX: Genesis A B C X Y Z = RetroPad Y B A L X R, Mode =
    # Select. Reading order, so three-button games sit on 1 2 3.
    "genesis": {1: "y", 2: "b", 3: "a", 4: "l", 5: "x", 6: "r",
                7: "start", 8: "select"},
    # MAME 2003-Plus: Button 1-6 = RetroPad B A Y X L R, Coin = Select.
    "arcade": {1: "b", 2: "a", 3: "y", 4: "x", 5: "l", 6: "r",
               7: "start", 8: "select"},
}


def user_entries(remap, player, below):
    """[(panel, target)] that a flat remap assigns to one player over the
    `below` layout, in the order overlay() must apply them."""
    picked = {}
    for key, target in (remap if isinstance(remap, dict) else {}).items():
        m = ENTRY.fullmatch(str(key))
        if not (m and isinstance(target, str) and target.lower() in TARGETS):
            continue
        own = m[1] is not None
        if (own and int(m[1]) == player) or (not own and int(m[2]) not in picked):
            picked[int(m[2])] = (own, target.lower())
    # A stored layer has no order (yaml sorts keys). /controls/map keeps one
    # claim per target, but older or hand-edited state can hold two; the
    # later-applied wins, so this player's keys beat `buttonN` and an entry
    # changing the layout below beats a stale one restating it.
    order = sorted((own, below.get(n) != t, n, t)
                   for n, (own, t) in picked.items())
    return [(n, t) for _, _, n, t in order]


def overlay(layout, entries):
    """Assigning N -> T first takes T off whichever button held it, so no
    RetroPad button is ever on two panel buttons."""
    for number, target in entries:
        for n in [n for n, t in layout.items() if t == target]:
            del layout[n]
        layout[number] = target
    return layout


def effective_layouts(bundle, system, rom_path=None):
    """{player: {panel number: target}} for players 1 and 2."""
    from renderer import game_key   # renderer imports this module
    games = (bundle.get("controllers/games.yaml") or {}).get("games") or {}
    glob = (bundle.get("controllers/global.yaml") or {}).get("buttons")
    sysmap = (bundle.get(f"controllers/systems/{system}.yaml") or {}).get("remap")
    gamemap = (games.get(game_key(system, rom_path)) or {}).get("remap")
    builtin = sorted(SYSTEM_LAYOUTS.get(system, {}).items())
    layouts = {}
    for player in PLAYERS:
        layout = overlay(dict(NINTENDO), user_entries(glob, player, NINTENDO))
        overlay(layout, builtin)
        for remap in (sysmap, gamemap):
            overlay(layout, user_entries(remap, player, layout))
        layouts[player] = layout
    return layouts


def load_profile():
    return json.loads(PROFILE.read_text())


def autoconfig(panel, layout, profile):
    """RetroArch autoconfig lines for one panel's virtual pad."""
    index = {int(n): code - BASE_CODE for n, code in profile["buttons"].items()}
    by_target = {t: index[n] for n, t in layout.items()}
    lines = [
        f"# {panel['name']}: panel buttons 1 2 3 4 / 5 6 7 8"
        " (cabinet/panel_layout.py)",
        'input_driver = "udev"',
        f'input_device = "{panel["name"]}"',
        f'input_vendor_id = "{VIRTUAL_VENDOR}"',
        f'input_product_id = "{panel["player"]}"',
    ]
    lines += [f'input_{t}_btn = "{by_target[t]}"' for t in TARGETS if t in by_target]
    lines += [f'input_{d}_btn = "h0{d}"' for d in ("up", "down", "left", "right")]
    return lines
