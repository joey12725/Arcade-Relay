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
# Launch shim path follows wherever the cabinet code actually lives
# (/cabinet in the sim, /opt/arcade/cabinet on provisioned hardware).
CABD_LAUNCH = os.environ.get(
    "CABD_LAUNCH",
    "python3 " + os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "cabd-launch"))

SYSTEM_EXTENSIONS = {
    "nes": "nes", "snes": "sfc,smc", "genesis": "md,bin,gen",
    "arcade": "zip,bin", "gba": "gba", "psx": "cue,chd,bin", "gb": "gb,gbc",
}


ART_DIR = os.path.join(ROMS_DIR, "art")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def art_lines(rom):
    """`assets.boxFront` for a rom, when the server has delivered its art.

    Emitted only when the PNG actually exists: Pegasus renders a broken-image
    box for a boxFront it cannot load, which looks worse than the theme's own
    generated cover. Art arrives via arcaded's /art/push (file.fetch into
    /roms/art), so a cabinet that has never been warmed just gets no line.

    NEVER indent metadata properties: in the Pegasus format a line with
    leading whitespace is a continuation of the previous property's VALUE,
    so an indented `file:`/`assets.boxFront:` silently disappears into the
    game title (verified on the real frontend — art only rendered once the
    indentation was removed).
    """
    png = os.path.join(ART_DIR, os.path.splitext(os.path.basename(rom))[0] + ".png")
    return [f"assets.boxFront: {png}"] if os.path.exists(png) else []


def gen_system_collections(bundle):
    """One metadata file per system dir under /roms.

    A per-rom `game:` block is emitted for every file present so box art can
    be attached; Pegasus still discovers anything unlisted through
    `extensions` + the collection launch line, so a rom that appears between
    renders is playable immediately (just un-arted until the next sync).
    """
    systems = (bundle.get("cabinet.yaml") or {}).get("systems") or []
    emulators = (bundle.get("cabinet.yaml") or {}).get("emulators") or {}
    installed = bundle.get("library/downloads.yaml") or {}
    written = []
    for sys_name in sorted(set(systems) | set(emulators)):
        ext = SYSTEM_EXTENSIONS.get(sys_name, "bin")
        lines = [f"collection: {sys_name.upper()}",
                 f"shortname: {sys_name}",
                 f"extensions: {ext}",
                 f'launch: {CABD_LAUNCH} {sys_name} "{{file.path}}"']
        d = os.path.join(ROMS_DIR, sys_name)
        companions = {f for gid, info in installed.items() if gid.startswith(sys_name + "/")
                      for f in info.get("files", []) if f != gid.split("/", 1)[1]}
        for f in sorted(companions):
            lines.append(f"ignore-file: {os.path.join(d, f)}")
        roms = []
        if os.path.isdir(d):
            roms = sorted(f for f in os.listdir(d)
                          if os.path.isfile(os.path.join(d, f))
                          and f != "metadata.pegasus.txt"
                          and not f.endswith(".tmp"))
        for rom in roms:
            if rom in companions:
                continue
            art = art_lines(rom)
            info = installed.get(sys_name + "/" + rom, {})
            if not art and not info:
                continue           # nothing to add beyond extension discovery
            lines.append("")
            lines.append(f"game: {info.get('title') or os.path.splitext(rom)[0]}")
            lines.append(f"file: {os.path.join(d, rom)}")
            lines.extend(art)
        written.append(_write(
            os.path.join(d, "metadata.pegasus.txt"), "\n".join(lines) + "\n"))
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
            lines.append(f'launch: {CABD_LAUNCH} {sys_name} "{{file.path}}"')
            lines.extend(art_lines(rom))
    return _write(os.path.join(ROMS_DIR, "favorites", "metadata.pegasus.txt"),
                  "\n".join(lines) + "\n")


def gen_streaming(bundle):
    """Streaming tiles (spec §5.8): stub .stream files + per-game launch.

    Each app entry may carry a `games:` list (managed from the control
    plane's Streaming tab). Those become their own tiles alongside the app
    itself, so 'Forza on Game Pass' is one launch from the cabinet rather
    than 'open Game Pass, then navigate'. A game with `enabled: false` is
    kept in state but not rendered — that is what the tab's ON CAB toggle
    means.
    """
    d = os.path.join(ROMS_DIR, "streaming")
    entries = (bundle.get("library/streaming.yaml") or {}).get("entries") or [
        {"name": "Steam Big Picture", "kind": "moonlight", "target": "Desktop"},
        {"name": "Game Pass Cloud", "kind": "greenlight", "target": "Game Pass Cloud"},
    ]
    lines = ["collection: Streaming", "shortname: streaming",
             "extensions: stream"]

    def tile(name, kind, target):
        fname = name.replace(" ", "_").replace("/", "_") + ".stream"
        _write(os.path.join(d, fname), f"{kind}:{target}\n")
        lines.append(f"game: {name}")
        lines.append(f"file: {os.path.join(d, fname)}")
        lines.append(f"launch: {CABD_LAUNCH} stream \"{kind}:{target}\"")

    for e in entries:
        tile(e["name"], e["kind"], e["target"])
        # streaming.yaml is hand-editable state; a malformed `games:` must
        # not take the whole render down (it runs on every state.sync)
        games = e.get("games")
        for g in games if isinstance(games, list) else []:
            if not isinstance(g, dict) or not g.get("enabled", True):
                continue
            if g.get("title") and g.get("target"):
                tile(g["title"], e["kind"], g["target"])
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
        # Steam art is keyed on appid (arcaded pulls it from Steam's CDN)
        lines.extend(art_lines(str(t["appid"])))
    return _write(os.path.join(d, "metadata.pegasus.txt"),
                  "\n".join(lines) + "\n")


def render_all(bundle):
    files = gen_system_collections(bundle)
    files.append(gen_favorites(bundle))
    files.append(gen_streaming(bundle))
    files.append(gen_steam(bundle))
    return files
