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


def render(bundle, system):
    """Returns list of written paths."""
    os.makedirs(RENDER_DIR, exist_ok=True)
    files = []

    g = (bundle.get("controllers/global.yaml") or {}).get("buttons", {})
    ra = (bundle.get("cabinet.yaml") or {}).get("retroarch") or {}
    lines = [f'input_player1_{k} = "{v}"' for k, v in sorted(g.items())]
    lines += [f'{k} = "{v}"' for k, v in sorted(ra.items())]
    files.append(_write(os.path.join(RENDER_DIR, "retroarch.cfg"), lines))

    sysmap = (bundle.get(f"controllers/systems/{system}.yaml") or {}).get("remap", {})
    files.append(_write(
        os.path.join(RENDER_DIR, f"{system}.rmp"),
        [f'{k} = "{v}"' for k, v in sorted(sysmap.items())]))

    return files
