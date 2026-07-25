#!/usr/bin/env python3
"""Config renderer (CTL-4, prime directive 3).

Renders emulator configs from the cached state bundle, deterministically,
before EVERY launch — whatever scrambled the rendered files at runtime, the
next launch is clean. Emulators never own mapping state.
"""
import os

RENDER_DIR = os.environ.get("RENDER_DIR", "/tmp/rendered")
HEADER = "# rendered by cabd from the state repo - local edits will be stomped"


def _write(path, lines):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join([HEADER, *lines]) + "\n")
    os.replace(tmp, path)   # atomic: a crashed render can't leave half a file
    return path


def game_key(system, rom_path):
    """Per-game mapping id: '<system>/<rom-basename>' (matches favorites)."""
    return f"{system}/{os.path.basename(rom_path)}" if rom_path else None


def render(bundle, system, rom_path=None):
    """Returns list of written paths.

    Mapping layers (spec §5.7, most specific wins): global buttons ->
    per-system remap -> per-game remap. The per-game layer is merged ON TOP
    of the system layer into the same .rmp the launch consumes, so a game
    with no overrides renders byte-identically to before.
    """
    os.makedirs(RENDER_DIR, exist_ok=True)
    files = []

    g = (bundle.get("controllers/global.yaml") or {}).get("buttons", {})
    ra = (bundle.get("cabinet.yaml") or {}).get("retroarch") or {}
    lines = [f'input_player1_{k} = "{v}"' for k, v in sorted(g.items())]
    lines += [f'{k} = "{v}"' for k, v in sorted(ra.items())]
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
