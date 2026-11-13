#!/usr/bin/env python3
"""ROM delivery adapter (ROM-1, spec §6.4): the server side of the game queue.

The queue's contract is search -> pick a candidate -> stage it on the server
-> verify -> deliver to the cabinet. This module owns the first three steps
and everything that has to be true about a file before the cabinet ever sees
it: checksums, archive CRCs, disc descriptors that only reference tracks we
actually shipped, a free-space reserve, cooperative cancellation, and
promotion into a shared library folder.

Two backends behind one contract:

  library — the owner's own game library on the server. Point ROM_SOURCE_DIR
            at a folder laid out as <system>/<file> (cartridge dumps you made
            yourself, homebrew, freeware releases) and the queue searches it,
            stages a verified copy, and ships it to the cabinet.
  fake    — deterministic generated ROM bytes, no files needed.

`ROMDL_BACKEND` picks one; `auto` (the default) uses the library when
ROM_SOURCE_DIR points at a directory. The sim pins `fake` because accept-m4
asserts on the fake payload's exact checksum.
"""
import difflib
import hashlib
import json
import os
import pathlib
import shutil
import urllib.parse
import urllib.request
import zipfile
import threading
import re
import shlex
import sys

STAGING = os.environ.get("ROMDL_STAGING", "/tmp/romdl-staging")
# Where the owner keeps game files the queue may hand out: <system>/<file>.
SOURCE_DIR = os.environ.get("ROM_SOURCE_DIR") or None
# Delivered games are promoted out of staging into ROM_LIBRARY/<system>/ and
# owned as ROM_LIBRARY_OWNER ("uid:gid"), so a share's users can manage what
# the root container writes. Unset, staging stays the only server-side copy.
LIBRARY = os.environ.get("ROM_LIBRARY") or None
OWNER = os.environ.get("ROM_LIBRARY_OWNER") or None
BACKEND = os.environ.get("ROMDL_BACKEND", "auto").lower()
COPY_TIMEOUT = int(os.environ.get("ROMDL_COPY_TIMEOUT", "3600"))
_cache_lock = threading.RLock()
MIN_FREE_BYTES = int(os.environ.get("ROMDL_MIN_FREE_BYTES", str(40 * 1024 ** 3)))

# Our /roms directory name -> a canonical console token. Several spellings
# people actually use map onto the same /roms folder.
SYSTEM_TO_CONSOLE = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gb": "gb", "gbc": "gbc",
    "gba": "gba", "nds": "nds", "3ds": "3ds",
    "genesis": "genesis", "megadrive": "genesis", "md": "genesis",
    "psx": "ps1", "ps1": "ps1", "ps2": "ps2", "ps3": "ps3", "psp": "psp",
    "psvita": "vita", "vita": "vita",
    "dreamcast": "dreamcast", "dc": "dreamcast", "saturn": "saturn",
    "arcade": "arcade", "mame": "arcade",
}

# Kept in the /roms/backend response for UI compatibility; the library
# backend has no remote sources that can go away.
DEAD_SOURCES = {}


def coverage(system):
    """Why a search for this system may come back empty, or None if it is
    expected to work. Advisory only; never blocks a search."""
    console = SYSTEM_TO_CONSOLE.get((system or "").lower())
    if console is None:
        return f"no console mapping for {system!r}"
    if using_real() and not _system_dirs(CONSOLE_TO_SYSTEM.get(console, system)):
        return f"no {system} folder under ROM_SOURCE_DIR"
    return None

# Reverse, for filing a download under a /roms dir.
CONSOLE_TO_SYSTEM = {
    "nes": "nes", "snes": "snes", "n64": "n64", "gb": "gb", "gbc": "gbc",
    "gba": "gba", "nds": "nds", "3ds": "3ds", "genesis": "genesis",
    "ps1": "psx", "ps2": "ps2", "ps3": "ps3", "psp": "psp", "vita": "psvita",
    "dreamcast": "dreamcast", "saturn": "saturn", "arcade": "arcade",
}

# Format support is independent of both source coverage and emulator setup.
# Do not add guessed console tokens: new source mappings need live validation.
LAUNCH_EXTENSIONS = {
    "nes": {".nes"}, "snes": {".sfc", ".smc"},
    "gb": {".gb", ".gbc"}, "gbc": {".gbc"}, "gba": {".gba"},
    "n64": {".n64", ".z64", ".v64"}, "nds": {".nds"},
    "3ds": {".3ds", ".cci", ".cxi"},
    "genesis": {".md", ".gen", ".bin"}, "arcade": {".zip"},
    "psx": {".cue", ".chd"}, "ps2": {".iso", ".chd", ".cue"},
    "psp": {".iso", ".cso", ".pbp"},
    "dreamcast": {".gdi", ".cdi", ".chd", ".cue"},
    "saturn": {".cue", ".chd", ".iso"},
}
ACQUISITION_EXTENSIONS = {
    **LAUNCH_EXTENSIONS,
    "3ds": LAUNCH_EXTENSIONS["3ds"] | {".cia"},
    "psp": LAUNCH_EXTENSIONS["psp"] | {".pkg"},
    "psvita": {".pkg", ".vpk"},
}
UNVERIFIED_SYSTEMS = ["gamecube", "wii", "atari2600", "atari7800", "xbox"]


def check_space(path, additional=0):
    if shutil.disk_usage(path).free - additional < MIN_FREE_BYTES:
        raise RuntimeError(f"server storage reserve reached at {path}")


def validate_descriptor(path, files):
    """Require every referenced track to be in the delivered manifest."""
    path = pathlib.Path(path)
    names = {f["filename"] for f in files}
    if path.suffix.lower() == ".cue":
        tracks = []
        for line in path.read_text().splitlines():
            if not re.match(r'^\s*FILE(?:\s|$)', line, re.I):
                continue
            try:
                row = shlex.split(line)
                if len(row) != 3:
                    raise ValueError("invalid FILE directive")
                tracks.append(row[1])
            except ValueError as e:
                raise RuntimeError("invalid CUE descriptor") from e
    elif path.suffix.lower() == ".gdi":
        lines = path.read_text().splitlines()
        try:
            count = int(lines[0])
            rows = [shlex.split(line) for line in lines[1:] if line.strip()]
            if count < 1 or len(rows) != count or any(len(row) != 6 for row in rows):
                raise ValueError("invalid track table")
            tracks = [row[4] for row in rows]
        except (ValueError, IndexError) as e:
            raise RuntimeError("invalid GDI descriptor") from e
    else:
        return
    if not tracks or any(t not in names or pathlib.PurePosixPath(t).name != t
                         or "\\" in t for t in tracks):
        raise RuntimeError("disc descriptor references missing or unsafe tracks")

class Cancelled(Exception):
    """A download stopped because the queue asked it to."""


class LibraryConflict(Exception):
    """A different file already holds a delivered game's library name."""


def _own(path):
    """Hand a staged or library path to OWNER. Never fatal: a file root still
    owns is an inconvenience on the share, not a failed download."""
    if not OWNER:
        return
    try:
        uid, gid = (int(x) for x in OWNER.split(":"))
        os.chown(path, uid, gid)
        os.chmod(path, 0o777 if os.path.isdir(path) else 0o666)
    except (OSError, ValueError) as e:
        print(f"romdl: cannot own {path} as {OWNER}: {e!r}", file=sys.stderr, flush=True)


FAKE_CATALOG = [
    {"id": "ms3", "title": "Metal Slug 3", "system": "arcade"},
    {"id": "ms1", "title": "Metal Slug", "system": "arcade"},
    {"id": "smw", "title": "Super Mario World", "system": "snes"},
    {"id": "sor2", "title": "Streets of Rage 2", "system": "genesis"},
    {"id": "mmz", "title": "Mega Man Zero", "system": "gba"},
    {"id": "sotn", "title": "Symphony of the Night", "system": "psx"},
]


def using_real():
    """True when a real (non-generated) backend serves the queue."""
    if BACKEND == "fake":
        return False
    if BACKEND in ("library", "real"):
        return True
    return bool(SOURCE_DIR) and os.path.isdir(SOURCE_DIR)


def backend_name():
    return "library" if using_real() else "fake"


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
    _own(p)


def _candidate_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def describe(rom_id):
    """{title, system} for a candidate id a search handed out, else None.

    Lets the queue label a job that was requested by bare id (the CLI's
    `roms request --id`) with the game it actually is, and refuse an id the
    downloader could never resolve.
    """
    if not using_real():
        entry = next((c for c in FAKE_CATALOG if c["id"] == rom_id), None)
    else:
        entry = _load_cache().get(rom_id)
    if entry is None:
        return None
    return {"title": entry["title"], "system": entry.get("system") or ""}


def remember_source(rom_id, metadata):
    if not metadata:
        return
    with _cache_lock:
        cache = _load_cache()
        entry = cache.get(rom_id)
        if entry and metadata.get("file_name") == entry["title"]:
            entry["source_metadata"] = metadata
            _save_cache(cache)


def _safe_filename(name):
    """Mirror of romdl.util.safe_filename — kept local so the adapter does not
    need romdl importable in this process."""
    import re
    name = re.sub(r"[^A-Za-z0-9._()\[\] -]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name[:180].rstrip() or "file")


def _hash_file(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _system_dirs(system):
    """Folders under SOURCE_DIR that hold games for one /roms system. Any
    alias that maps to the same console counts (genesis/megadrive/md)."""
    if not SOURCE_DIR:
        return []
    console = SYSTEM_TO_CONSOLE.get((system or "").lower())
    root = pathlib.Path(SOURCE_DIR)
    names = {s for s, c in SYSTEM_TO_CONSOLE.items() if c == console} or {system}
    return [root / n for n in sorted(names) if (root / n).is_dir()]


def _inside_source(path):
    """True when `path` resolves to a file inside SOURCE_DIR (no symlink or
    `..` escapes); the queue never copies anything else."""
    if not SOURCE_DIR:
        return False
    try:
        return pathlib.Path(path).resolve().is_relative_to(
            pathlib.Path(SOURCE_DIR).resolve())
    except OSError:
        return False


_REGION = re.compile(r"[\(\[][^\)\]]*[\)\]]")


def _normalize(title):
    """'Game Title (USA) (Rev 1).zip' -> 'game title'."""
    stem = pathlib.PurePath(title).stem if pathlib.PurePath(title).suffix else title
    stem = _REGION.sub(" ", stem)
    return " ".join(re.findall(r"[a-z0-9]+", stem.lower()))


def score(query, title):
    """Title match score. >= 100 means "confident enough to queue unattended":
    an exact title is 150, every query word present is 100-135, anything
    else is a fuzzy ratio below 100 that only a human pick can queue."""
    q, t = _normalize(query), _normalize(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 150.0
    words = q.split()
    have = set(t.split())
    if all(w in have for w in words):
        return round(100 + 35 * len(words) / max(len(have), 1), 1)
    return round(99 * difflib.SequenceMatcher(None, q, t).ratio(), 1)


# -------------------------------------------------------------------- search

def search(query, system=None, limit=10, deep=False):
    """Candidates for a query. Returns [{id, title, system, source, score}].

    `system` is one of our /roms directory names. The library backend needs
    it (it files the delivery under that system's folder on the cabinet), so
    it is required there and optional for the fake. `deep` also matches
    files nested in sub-folders of the system directory.
    """
    if not using_real():
        q = query.lower()
        return [dict(c, source="fake", score=100.0)
                for c in FAKE_CATALOG if q in c["title"].lower()]

    if not system:
        raise ValueError("a system is required (the library is filed per system)")
    console = SYSTEM_TO_CONSOLE.get(system.lower())
    if console is None:
        raise ValueError(f"no console mapping for system {system!r}")
    canonical = CONSOLE_TO_SYSTEM[console]
    allowed = ACQUISITION_EXTENSIONS.get(canonical, set()) | {".zip"}

    found = []
    for folder in _system_dirs(canonical):
        files = folder.rglob("*") if deep else folder.iterdir()
        for path in files:
            if (path.is_file() and path.suffix.lower() in allowed
                    and _inside_source(path)):
                found.append((score(query, path.name), path))
    found.sort(key=lambda m: (-m[0], m[1].name))

    cache = _load_cache()
    out = []
    for points, path in found[:limit]:
        url = path.resolve().as_uri()
        rid = _candidate_id(url)
        entry = {**cache.get(rid, {}), "id": rid, "title": path.name,
                 "system": canonical, "console": console, "url": url,
                 "extra": {}, "source": "library", "score": points}
        cache[rid] = entry
        out.append({k: entry[k] for k in
                    ("id", "title", "system", "source", "score")})
    with _cache_lock:
        merged = _load_cache()
        merged.update(cache)
        _save_cache(merged)
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
    _own(rom_path)
    _own(art_path)
    return {"rom_path": rom_path, "art_path": art_path,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "system": entry["system"], "filename": fname,
            "title": entry["title"]}


def download(rom_id, cancel=None, progress=None):
    """Stage a candidate on the server and verify it.

    Returns {rom_path, art_path|None, sha256, system, filename, title}. The
    library backend has no artwork source (arcaded fills that from artdb
    after delivery), so art_path is None there.

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
    check_space(STAGING)
    fname = _safe_filename(entry["title"])
    directory = pathlib.Path(STAGING) / rom_id
    directory.mkdir(exist_ok=True)
    _own(directory)
    rom_path = str(directory / fname)
    tmp = rom_path + ".part"
    h = hashlib.sha256()
    source_info = None
    url = entry["url"]
    retained = entry.get("source_metadata")
    reuse = bool(retained and os.path.isfile(rom_path)
                 and os.path.getsize(rom_path) == retained["size"]
                 and _hash_file(rom_path) == retained["sha256"])
    if reuse:
        source_info = retained
        url = pathlib.Path(rom_path).as_uri()
    elif url.startswith("file:"):
        src = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
        if not _inside_source(src) or not os.path.isfile(src):
            raise RuntimeError("source file is outside ROM_SOURCE_DIR or gone")
        # Hash the source up front: the staged copy must match it byte for
        # byte, and the digest is the provenance record the queue keeps.
        source_info = {"file_name": entry["title"], "size": os.path.getsize(src),
                       "sha256": _hash_file(src)}
    else:
        raise RuntimeError("only files under ROM_SOURCE_DIR can be delivered")
    req = urllib.request.Request(url)
    done = 0
    try:
        with urllib.request.urlopen(req, timeout=COPY_TIMEOUT) as r, \
                open(tmp, "wb") as f:
            total = None
            try:
                total = int(r.headers.get("Content-Length") or 0) or None
            except (TypeError, ValueError):
                pass
            check_space(directory, total or 0)
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled(f"cancelled after {done} bytes")
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                check_space(directory, len(chunk))
                h.update(chunk)
                f.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
            if total is not None and done != total:
                raise RuntimeError(f"source length mismatch: expected {total}, received {done}")
            if done == 0:
                raise RuntimeError("source returned an empty file")
            if entry.get("sha256") and h.hexdigest() != entry["sha256"]:
                raise RuntimeError("source checksum mismatch")
            if source_info and (done != source_info["size"] or h.hexdigest() != source_info["sha256"]):
                raise RuntimeError("source checksum or size changed during the copy")
            if fname.lower().endswith(".zip"):
                f.flush()
                with zipfile.ZipFile(tmp) as z:
                    bad = z.testzip()
                    if bad:
                        raise RuntimeError(f"archive CRC mismatch: {bad}")
                    if entry["system"] == "arcade":
                        members = {i.filename: i for i in z.infolist()}
                        for rom in entry.get("extra", {}).get("roms", []):
                            i = members.get(rom["name"])
                            if i is None or i.file_size != int(rom["size"]) or (rom.get("crc") and i.CRC != int(rom["crc"], 16)):
                                raise RuntimeError(f"MAME2003-plus ROM mismatch: {rom['name']}")
    except BaseException:
        # never leave a partial behind for the delivery step to pick up
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    os.replace(tmp, rom_path)
    _own(rom_path)
    if source_info:
        entry["source_metadata"] = source_info
        with _cache_lock:
            cache = _load_cache()
            cache[rom_id] = entry
            _save_cache(cache)

    # Cartridge ZIPs contain one launchable ROM; PS1 ZIPs contain a cue and
    # its tracks. Keep arcade ZIPs intact, as MAME uses the short set name.
    files = []
    if fname.lower().endswith(".zip") and entry["system"] != "arcade":
        target = pathlib.Path(STAGING) / rom_id
        target.mkdir(exist_ok=True)
        with zipfile.ZipFile(rom_path) as z:
            check_space(target, sum(i.file_size for i in z.infolist()))
            seen = set()
            for i in z.infolist():
                p = pathlib.PurePosixPath(i.filename)
                if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
                    raise RuntimeError(f"unsupported archive path: {i.filename}")
                if i.is_dir():
                    continue
                if i.filename in seen or i.filename == pathlib.Path(rom_path).name:
                    raise RuntimeError(f"duplicate or conflicting archive member: {i.filename}")
                seen.add(i.filename)
                check_space(target, i.file_size)
                z.extract(i, target)
                _own(target / i.filename)
                files.append({"filename": i.filename, "rom_path": str(target / i.filename),
                              "sha256": _hash_file(target / i.filename)})
        launch = [f for f in files if pathlib.Path(f["filename"]).suffix.lower()
                  in ACQUISITION_EXTENSIONS.get(entry["system"], set())]
        if len(launch) != 1:
            raise RuntimeError(f"archive must contain one primary game file, found {len(launch)}")
        rom_path, fname = launch[0]["rom_path"], launch[0]["filename"]
        validate_descriptor(rom_path, files)
        digest = _hash_file(rom_path)
    else:
        if pathlib.Path(fname).suffix.lower() not in ACQUISITION_EXTENSIONS.get(entry["system"], set()):
            raise RuntimeError(f"unsupported {entry['system']} acquisition format: {fname}")
        validate_descriptor(rom_path, [])
        digest = h.hexdigest()

    system = entry.get("system") or CONSOLE_TO_SYSTEM.get(
        entry.get("console", ""), "unknown")
    return {"rom_path": rom_path, "art_path": None,
            "sha256": digest, "system": system,
            "filename": fname, "title": entry["title"], "files": files,
            "source_url": entry["url"], "bytes": done,
            "source_metadata": source_info,
            "source_reused": reuse,
            "display_title": entry.get("extra", {}).get("display_title")}


# ------------------------------------------------------------------- library

def promote(result):
    """Move a delivered game from staging into LIBRARY/<system>/.

    `result` is what download() returned. Returns the library paths, or []
    without LIBRARY. A library file with the same bytes counts as promoted
    and the staged copy is dropped; a different file under the same name
    raises LibraryConflict before anything moves. The item's folder under
    STAGING (source archive included) goes once every payload is placed.
    """
    if not LIBRARY:
        return []
    system = result.get("system") or ""
    if not re.fullmatch(r"[a-z0-9]+", system):
        raise ValueError(f"unsafe library system {system!r}")
    payloads = result.get("files") or [result]
    for f in payloads:
        name = f.get("filename") or ""
        if "/" in name or name in ("", ".", ".."):
            raise ValueError(f"unsafe library file name {name!r}")
    root = pathlib.Path(LIBRARY) / system
    plan = []
    for f in payloads:
        dest = root / f["filename"]
        if dest.exists() and _hash_file(dest) != f["sha256"]:
            raise LibraryConflict(f"{dest} already holds a different file")
        plan.append((pathlib.Path(f["rom_path"]), dest, f["sha256"]))
    if not root.is_dir():
        root.mkdir(exist_ok=True)
        _own(root)
    placed = []
    for src, dest, digest in plan:
        if dest.exists():
            src.unlink(missing_ok=True)
        else:
            try:
                os.replace(src, dest)
            except OSError:
                # staging and library on different filesystems
                part = dest.with_name(dest.name + ".part")
                try:
                    shutil.copyfile(src, part)
                    if _hash_file(part) != digest:
                        raise RuntimeError(f"library copy checksum mismatch: {dest}")
                    os.replace(part, dest)
                finally:
                    part.unlink(missing_ok=True)
                src.unlink()
        _own(dest)
        placed.append(str(dest))
    staging = pathlib.Path(os.path.realpath(STAGING))
    for src, _, _ in plan:
        src = pathlib.Path(os.path.realpath(src))
        if src.is_relative_to(staging) and src != staging:
            top = staging / src.relative_to(staging).parts[0]
            if top.is_dir():
                shutil.rmtree(top)
    return placed
