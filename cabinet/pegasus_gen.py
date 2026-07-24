#!/usr/bin/env python3
"""Generate Pegasus collections/metadata from the state bundle (spec §5.3).

Pegasus reads `metadata.pegasus.txt` files. Everything launches through
cabd-launch (never exec'ing emulators directly), so cabd always knows
what's running. Favorites are mirrored from state/library/favorites.yaml
into a first-class Favorites collection; Streaming and Steam collections
come from state too. Regenerated on every state.sync and before the
frontend starts — local edits are stomped like every other rendered config.
"""
import os

ROMS_DIR = os.environ.get("CABD_ROMS_DIR", "/roms")
CABD_LAUNCH = "python3 /cabinet/cabd-launch"

SYSTEM_EXTENSIONS = {
    "nes": "nes", "snes": "sfc,smc", "genesis": "md,bin,gen",
    "arcade": "zip,bin", "gba": "gba", "psx": "cue,chd,bin", "gb": "gb,gbc",
}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def gen_system_collections(bundle):
    """One metadata file per system dir under /roms."""
    systems = (bundle.get("cabinet.yaml") or {}).get("systems") or []
    emulators = (bundle.get("cabinet.yaml") or {}).get("emulators") or {}
    written = []
    for sys_name in sorted(set(systems) | set(emulators)):
        ext = SYSTEM_EXTENSIONS.get(sys_name, "bin")
        text = (f"collection: {sys_name.upper()}\n"
                f"shortname: {sys_name}\n"
                f"extensions: {ext}\n"
                f"launch: {CABD_LAUNCH} {sys_name} {{file.path}}\n")
        written.append(_write(
            os.path.join(ROMS_DIR, sys_name, "metadata.pegasus.txt"), text))
    return written


def gen_favorites(bundle):
    """Favorites as an explicit collection (UI-2: default boot view — the
    theme sorts it first). Entries are '<system>/<rom-basename>'."""
    favs = (bundle.get("library/favorites.yaml") or {}).get("favorites") or []
    # No collection-level launch fallback: cabd-launch needs <system> <rom>,
    # which only the per-game launch lines below can provide. A collection
    # fallback without the system arg would exit 2 if it ever fired.
    lines = ["collection: Favorites", "shortname: favorites"]
    for fav in sorted(favs):
        sys_name, _, rom = fav.partition("/")
        if sys_name and rom:
            lines.append(f"game: {rom}")
            lines.append(f"file: {os.path.join(ROMS_DIR, sys_name, rom)}")
            lines.append(f"launch: {CABD_LAUNCH} {sys_name} {{file.path}}")
    return _write(os.path.join(ROMS_DIR, "favorites", "metadata.pegasus.txt"),
                  "\n".join(lines) + "\n")


def gen_streaming(bundle):
    """Streaming tiles (spec §5.8): stub .stream files + per-game launch."""
    d = os.path.join(ROMS_DIR, "streaming")
    entries = (bundle.get("library/streaming.yaml") or {}).get("entries") or [
        {"name": "Steam Big Picture", "kind": "moonlight", "target": "Desktop"},
        {"name": "Game Pass Cloud", "kind": "greenlight", "target": "Game Pass Cloud"},
    ]
    lines = ["collection: Streaming", "shortname: streaming",
             "extensions: stream"]
    for e in entries:
        fname = e["name"].replace(" ", "_") + ".stream"
        _write(os.path.join(d, fname), f"{e['kind']}:{e['target']}\n")
        lines.append(f"game: {e['name']}")
        lines.append(f"file: {os.path.join(d, fname)}")
        lines.append(f"launch: {CABD_LAUNCH} stream \"{e['kind']}:{e['target']}\"")
    return _write(os.path.join(d, "metadata.pegasus.txt"),
                  "\n".join(lines) + "\n")


def gen_steam(bundle):
    """Local Steam allowlist (EMU-4, spec §5.9)."""
    d = os.path.join(ROMS_DIR, "steam")
    titles = (bundle.get("library/steam.yaml") or {}).get("titles") or []
    lines = ["collection: Steam", "shortname: steam", "extensions: steamid"]
    for t in titles:
        fname = t["name"].replace(" ", "_") + ".steamid"
        _write(os.path.join(d, fname), str(t["appid"]) + "\n")
        lines.append(f"game: {t['name']}")
        lines.append(f"file: {os.path.join(d, fname)}")
        lines.append(f"launch: {CABD_LAUNCH} steam {t['appid']}")
    return _write(os.path.join(d, "metadata.pegasus.txt"),
                  "\n".join(lines) + "\n")


def render_all(bundle):
    files = gen_system_collections(bundle)
    files.append(gen_favorites(bundle))
    files.append(gen_streaming(bundle))
    files.append(gen_steam(bundle))
    return files
