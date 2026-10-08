#!/usr/bin/env python3
"""Config renderer (CTL-4, prime directive 3).

Renders emulator configs from the cached state bundle, deterministically,
before EVERY launch — whatever scrambled the rendered files at runtime, the
next launch is clean. Emulators never own mapping state.
"""
import os
import shutil

import panel_layout

RENDER_DIR = os.environ.get("RENDER_DIR", "/tmp/rendered")
STOCK_AUTOCONFIG = "/usr/share/libretro/autoconfig/udev"   # install.sh puts them here
HEADER = "# rendered by cabd from the state repo - local edits will be stomped"


def _write(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join([HEADER, *lines]) + "\n")
    os.replace(tmp, path)   # atomic: a crashed render can't leave half a file
    return path


def _render_panel_autoconfig(layouts, base_dir):
    """Both panels' profiles plus the stock ones symlinked alongside (other
    pads still autoconfigure). Anything else in the dir, e.g. a link whose
    stock profile is gone, is pruned so it cannot shadow a fresh render."""
    out = os.path.join(RENDER_DIR, "autoconfig")
    if os.path.islink(out) or os.path.isfile(out):
        os.unlink(out)
    os.makedirs(out, exist_ok=True)
    profile = panel_layout.load_profile()
    ours = {p["name"].replace(" ", "_") + ".cfg": p for p in profile["panels"]}
    stock = {}
    if (base_dir and os.path.isdir(base_dir)
            and os.path.realpath(base_dir) != os.path.realpath(out)):
        stock = {n: os.path.join(base_dir, n) for n in os.listdir(base_dir)
                 if n.endswith(".cfg") and n not in ours
                 and os.path.isfile(os.path.join(base_dir, n))}
    for name in os.listdir(out):
        path = os.path.join(out, name)
        if os.path.islink(path):
            if os.readlink(path) != stock.get(name):
                os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        elif name not in ours:
            os.unlink(path)
    for name, target in stock.items():
        if not os.path.lexists(os.path.join(out, name)):
            os.symlink(target, os.path.join(out, name))
    files = [_write(os.path.join(out, name), panel_layout.autoconfig(
                 panel, layouts[panel["player"]], profile))
             for name, panel in ours.items()]
    return files, out


def game_key(system, rom_path):
    """Per-game mapping id: '<system>/<rom-basename>' (matches favorites)."""
    return f"{system}/{os.path.basename(rom_path)}" if rom_path else None


def render(bundle, system, rom_path=None):
    """Returns list of written paths.

    Mapping layers (spec §5.7, most specific wins): global buttons ->
    per-system remap -> per-game remap. Physical panel keys reach RetroArch
    as the panels' autoconfig (panel_layout.py). Logical keys keep their
    legacy render: keyboard binds in retroarch.cfg and the <system>.rmp,
    which only the sim reads; the per-game layer is merged ON TOP of the
    system layer there, so a game with no overrides renders identically.
    """
    os.makedirs(RENDER_DIR, exist_ok=True)
    files = []

    g = (bundle.get("controllers/global.yaml") or {}).get("buttons", {})
    ra = (bundle.get("cabinet.yaml") or {}).get("retroarch") or {}
    # physical panel keys are applied via autoconfig below, not as binds
    lines = [f'input_player1_{k} = "{v}"' for k, v in sorted(g.items())
             if not panel_layout.PHYSICAL.fullmatch(str(k))]
    panel_files, autoconfig_dir = _render_panel_autoconfig(
        panel_layout.effective_layouts(bundle, system, rom_path),
        ra.get("joypad_autoconfig_dir") or STOCK_AUTOCONFIG)
    ra = dict(ra, joypad_autoconfig_dir=autoconfig_dir)
    lines += [f'{k} = "{v}"' for k, v in sorted(ra.items())]
    files += panel_files
    files.append(_write(os.path.join(RENDER_DIR, "retroarch.cfg"), lines))

    remap = dict((bundle.get(f"controllers/systems/{system}.yaml")
                  or {}).get("remap", {}))
    key = game_key(system, rom_path)
    if key:
        games = (bundle.get("controllers/games.yaml") or {}).get("games") or {}
        remap.update((games.get(key) or {}).get("remap", {}))
    files.append(_write(
        os.path.join(RENDER_DIR, f"{system}.rmp"),
        [f'{k} = "{v}"' for k, v in sorted(remap.items())]))

    return files
