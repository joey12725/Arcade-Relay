#!/usr/bin/env python3
"""ROMDL adapter (ROM-1, spec §6.4) — bridges the queue to the romdl CLI.

Two backends behind one contract:

  real — shells out to the `romdl` CLI's documented `--json` interface
         (example.invalid/romdl). Searches Mirror + PkgIndex.
  fake — deterministic generated ROM bytes, no network.

`ROMDL_BACKEND` picks one; `auto` (the default) uses real when the CLI is on
PATH. The sim pins `fake` because its network is a closed toxiproxy topology
with no egress, and accept-m4 asserts on the fake payload's exact checksum.

Why the CLI and not `import romdl`: the README advertises `--json` as the
automation interface, so it is the part with a stability promise. Importing
would couple us to `_unified_search`, a private function, and would drag
requests/bs4/rapidfuzz into the arcaded process — the Dockerfile installs
romdl into its own venv precisely so it cannot.

Download does NOT re-run `romdl download`. Search caches each candidate's URL
against the id the queue holds, and download streams that exact URL: the
queue's job is to fetch *the thing the user picked*, and a second search
could rank a different dump first.
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import urllib.request

STAGING = os.environ.get("ROMDL_STAGING", "/tmp/romdl-staging")
BACKEND = os.environ.get("ROMDL_BACKEND", "auto").lower()
ROMDL_BIN = os.environ.get("ROMDL_BIN", "romdl")
SEARCH_TIMEOUT = int(os.environ.get("ROMDL_SEARCH_TIMEOUT", "180"))
DOWNLOAD_TIMEOUT = int(os.environ.get("ROMDL_DOWNLOAD_TIMEOUT", "3600"))
USER_AGENT = "arcaded/1.0 (+romdl adapter)"

# Our /roms directory name -> the console token romdl's fuzzy matcher wants.
# Both of its sources keep their own alias tables; these are the spellings
# that hit in both.
SYSTEM_TO_CONSOLE = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gb": "gb", "gbc": "gbc",
    "gba": "gba", "nds": "nds", "3ds": "3ds",
    "genesis": "genesis", "megadrive": "genesis", "md": "genesis",
    "psx": "ps1", "ps1": "ps1", "ps2": "ps2", "ps3": "ps3", "psp": "psp",
    "psvita": "vita", "vita": "vita",
    "dreamcast": "dreamcast", "dc": "dreamcast", "saturn": "saturn",
    "arcade": "arcade", "mame": "arcade",
}
# PkgIndex only carries Sony digital content. romdl asks it for every
# console anyway, and its `_normalize_console_keys` falls back to querying
# ALL of its databases when the console name matches none of its aliases —
# so a `snes` search comes back topped by PS Vita titles that merely share a
# word with the query, and they outrank anything else on score. Drop them
# here rather than hand the queue a candidate that would deliver a Vita file
# into /roms/snes.
NPS_CONSOLES = {"ps1", "psp", "ps3", "vita"}

# Mirror the ORIGINAL host shut down 2026-03-31; the romdl `mirror`
# source was repointed (a 2026-04 patch) at mirror.invalid,
# a successor mirror with the same autoindex layout, so the source id
# "mirror" lives on with a different upstream. If cartridge-console
# searches go empty again, check that the deployed romdl venv still carries
# that patch — a fresh image built from an unpatched romdl repo regresses to
# the dead host.
DEAD_SOURCES = {}


def coverage(system):
    """Why a search for this system may come back empty, or None if it is
    expected to work. Advisory only — never blocks a search."""
    console = SYSTEM_TO_CONSOLE.get((system or "").lower())
    if console is None:
        return f"no ROMDL console mapping for {system!r}"
    return None

# Reverse, for filing a download under a /roms dir.
CONSOLE_TO_SYSTEM = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gb": "gb", "gbc": "gbc",
    "gba": "gba", "nds": "nds", "3ds": "3ds", "genesis": "genesis",
    "ps1": "psx", "ps2": "ps2", "ps3": "ps3", "psp": "psp", "vita": "psvita",
    "dreamcast": "dreamcast", "saturn": "saturn", "arcade": "arcade",
}

class Cancelled(Exception):
    """A download stopped because the queue asked it to."""


FAKE_CATALOG = [
    {"id": "ms3", "title": "Metal Slug 3", "system": "arcade"},
    {"id": "ms1", "title": "Metal Slug", "system": "arcade"},
    {"id": "smw", "title": "Super Mario World", "system": "snes"},
    {"id": "sor2", "title": "Streets of Rage 2", "system": "genesis"},
    {"id": "mmz", "title": "Mega Man Zero", "system": "gba"},
    {"id": "sotn", "title": "Symphony of the Night", "system": "psx"},
]


def using_real():
    if BACKEND == "fake":
        return False
    if BACKEND == "real":
        return True
    return shutil.which(ROMDL_BIN) is not None


def backend_name():
    return "real" if using_real() else "fake"


# --------------------------------------------------------------- candidates
# Search results are cached to disk so an id handed out before a restart is
# still downloadable afterwards — the queue survives arcaded, the id must too.

def _cache_path():
    return pathlib.Path(STAGING) / "candidates.json"


def _load_cache():
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # keep the file bounded; ids are cheap to regenerate by searching again
    if len(cache) > 2000:
        cache = dict(list(cache.items())[-1000:])
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, p)


def _candidate_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _safe_filename(name):
    """Mirror of romdl.util.safe_filename — kept local so the adapter does not
    need romdl importable in this process."""
    import re
    name = re.sub(r"[^A-Za-z0-9._()\[\] -]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:180].rstrip() or "file")


# -------------------------------------------------------------------- search

def _run_romdl(args, timeout):
    proc = subprocess.run([ROMDL_BIN, "--json", *args],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode == 2:          # documented "no results"
        return []
    if proc.returncode != 0:
        raise RuntimeError(
            f"romdl {' '.join(args)} failed ({proc.returncode}): "
            + (proc.stderr or "").strip()[:300])
    try:
        return json.loads(proc.stdout or "[]")
    except ValueError as e:
        raise RuntimeError(f"romdl returned non-JSON: {e}") from e


def search(query, system=None, limit=10, deep=False):
    """Candidates for a query. Returns [{id, title, system, source, score}].

    `system` is one of our /roms directory names. The real backend needs it —
    romdl searches one console at a time and crawling every console per query
    would take minutes — so it is required there and optional for the fake.
    """
    if not using_real():
        q = query.lower()
        return [dict(c, source="fake", score=100.0)
                for c in FAKE_CATALOG if q in c["title"].lower()]

    if not system:
        raise ValueError(
            "a system is required (romdl searches one console at a time)")
    console = SYSTEM_TO_CONSOLE.get(system.lower())
    if console is None:
        raise ValueError(f"no romdl console mapping for system {system!r}")

    args = ["search", console, query, "-n", str(limit)]
    if deep:
        args.append("--deep")
    matches = _run_romdl(args, SEARCH_TIMEOUT)

    cache = _load_cache()
    out = []
    for m in matches:
        url = m.get("url")
        if not url:
            continue
        if (m.get("source") == "pkgindex" and console not in NPS_CONSOLES):
            continue
        rid = _candidate_id(url)
        entry = {"id": rid, "title": m.get("title") or url.rsplit("/", 1)[-1],
                 "system": system.lower(), "console": console, "url": url,
                 "source": m.get("source", "?"),
                 "score": round(float(m.get("score", 0)), 1)}
        cache[rid] = entry
        out.append({k: entry[k] for k in
                    ("id", "title", "system", "source", "score")})
    _save_cache(cache)
    return out


# ------------------------------------------------------------------ download

def _download_fake(rom_id):
    entry = next((c for c in FAKE_CATALOG if c["id"] == rom_id), None)
    if entry is None:
        raise KeyError(f"unknown rom id {rom_id}")
    os.makedirs(STAGING, exist_ok=True)
    fname = entry["title"].replace(" ", "_") + ".bin"
    rom_path = os.path.join(STAGING, fname)
    blob = hashlib.sha256(rom_id.encode()).digest() * 8192   # 256KB
    with open(rom_path, "wb") as f:
        f.write(blob)
    art_path = os.path.join(STAGING, fname + ".png")
    # tiny valid-enough art placeholder (the theme only needs a file)
    with open(art_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n"
                + hashlib.sha256((rom_id + "art").encode()).digest())
    return {"rom_path": rom_path, "art_path": art_path,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "system": entry["system"], "filename": fname,
            "title": entry["title"]}


def download(rom_id, cancel=None, progress=None):
    """Fetch a candidate into staging.

    Returns {rom_path, art_path|None, sha256, system, filename, title}. The
    real backend has no artwork source — arcaded fills that from artdb after
    delivery — so art_path is None there.

    `cancel` is a threading.Event checked between chunks: /roms/cancel sets
    it, the transfer stops with `Cancelled`, and the partial is removed.
    `progress(done, total|None)` runs per chunk for the UI's transfer bar.
    """
    if not using_real():
        return _download_fake(rom_id)

    entry = _load_cache().get(rom_id)
    if entry is None:
        raise KeyError(f"unknown rom id {rom_id} (search again to refresh it)")

    os.makedirs(STAGING, exist_ok=True)
    fname = _safe_filename(entry["title"])
    rom_path = os.path.join(STAGING, fname)
    tmp = rom_path + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(entry["url"],
                                 headers={"User-Agent": USER_AGENT})
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r, \
                open(tmp, "wb") as f:
            total = None
            try:
                total = int(r.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                pass
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled(f"cancelled after {done} bytes")
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                f.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except BaseException:
        # never leave a partial behind for the delivery step to pick up
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, rom_path)

    system = entry.get("system") or CONSOLE_TO_SYSTEM.get(
        entry.get("console", ""), "unknown")
    return {"rom_path": rom_path, "art_path": None,
            "sha256": h.hexdigest(), "system": system,
            "filename": fname, "title": entry["title"]}
