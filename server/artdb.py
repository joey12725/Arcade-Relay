#!/usr/bin/env python3
"""artdb — box art and screenshots for the library (libretro-thumbnails).

Why libretro-thumbnails and not ScreenScraper/IGDB/TheGamesDB: it needs no
account, no API key and no OAuth refresh, and it is keyed on the *No-Intro /
Redump ROM name* — which is exactly what a `/roms` tree holds. That makes the
common case a single deterministic URL with no search step at all.

Three lookup tiers, cheapest first:

  1. direct  — sanitize the ROM stem per the libretro naming rule and try the
               URL. Hits immediately for any correctly-named dump.
  2. variant — article swaps ("Zelda, The" <-> "The Zelda"), tag stripping,
               and the usual region suffixes. Covers lightly-renamed sets.
  3. index   — fetch the system's directory listing once, cache it, and match
               on a loose normalized key. Covers everything else, including
               sets with scene tags or wrong regions.

Every result (including misses) is cached on disk, so a cabinet with no
internet egress still serves art once it has been warmed. Misses expire so a
thumbnail added upstream is eventually picked up.
"""
import asyncio
import json
import os
import pathlib
import re
import time
import urllib.parse

import aiohttp

THUMB_BASE = os.environ.get("ARTDB_BASE", "https://thumbnails.libretro.com")
STEAM_BASE = os.environ.get("ARTDB_STEAM_BASE",
                            "https://cdn.cloudflare.steamstatic.com/steam/apps")
CACHE_DIR = pathlib.Path(os.environ.get("ARTDB_CACHE", "/art-cache"))
MISS_TTL = float(os.environ.get("ARTDB_MISS_TTL", 24 * 3600))
INDEX_TTL = float(os.environ.get("ARTDB_INDEX_TTL", 7 * 24 * 3600))
TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=30)

# Our system directory name -> the libretro-thumbnails system name. Keys are
# what a `/roms/<system>` dir is actually called; several aliases map onto the
# same upstream because ROM sets disagree about e.g. genesis/megadrive.
PLATFORMS = {
    "nes": "Nintendo - Nintendo Entertainment System",
    "fds": "Nintendo - Family Computer Disk System",
    "snes": "Nintendo - Super Nintendo Entertainment System",
    "sfc": "Nintendo - Super Nintendo Entertainment System",
    "n64": "Nintendo - Nintendo 64",
    "ngc": "Nintendo - GameCube",
    "gamecube": "Nintendo - GameCube",
    "wii": "Nintendo - Wii",
    "gb": "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color",
    "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS",
    "3ds": "Nintendo - Nintendo 3DS",
    "vb": "Nintendo - Virtual Boy",
    "genesis": "Sega - Mega Drive - Genesis",
    "megadrive": "Sega - Mega Drive - Genesis",
    "md": "Sega - Mega Drive - Genesis",
    "sms": "Sega - Master System - Mark III",
    "mastersystem": "Sega - Master System - Mark III",
    "gg": "Sega - Game Gear",
    "gamegear": "Sega - Game Gear",
    "segacd": "Sega - Mega-CD - Sega CD",
    "sega32x": "Sega - 32X",
    "saturn": "Sega - Saturn",
    "dreamcast": "Sega - Dreamcast",
    "dc": "Sega - Dreamcast",
    "psx": "Sony - PlayStation",
    "ps1": "Sony - PlayStation",
    "playstation": "Sony - PlayStation",
    "ps2": "Sony - PlayStation 2",
    "psp": "Sony - PlayStation Portable",
    "arcade": "MAME",
    "mame": "MAME",
    "fbneo": "FBNeo - Arcade Games",
    "neogeo": "SNK - Neo Geo",
    "ngp": "SNK - Neo Geo Pocket",
    "ngpc": "SNK - Neo Geo Pocket Color",
    "pcengine": "NEC - PC Engine - TurboGrafx 16",
    "tg16": "NEC - PC Engine - TurboGrafx 16",
    "3do": "The 3DO Company - 3DO",
    "atari2600": "Atari - 2600",
    "atari7800": "Atari - 7800",
    "lynx": "Atari - Lynx",
    "jaguar": "Atari - Jaguar",
    "wonderswan": "Bandai - WonderSwan",
    "msx": "Microsoft - MSX",
    "c64": "Commodore - 64",
    "amiga": "Commodore - Amiga",
    "dos": "DOS",
    "scummvm": "ScummVM",
}

# The three thumbnail sets every libretro-thumbnails repo carries.
KINDS = {"box": "Named_Boxarts", "title": "Named_Titles", "snap": "Named_Snaps"}

# libretro's own naming rule: these characters are replaced with '_' when a
# ROM name is turned into a thumbnail filename. Everything else is verbatim.
_ILLEGAL = re.compile(r"[&*/:`<>?\\|]")
_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_LOOSE = re.compile(r"[^a-z0-9]+")
COMMON_REGIONS = ("(USA)", "(World)", "(Europe)", "(Japan)", "(USA, Europe)",
                  "(Japan, USA)", "(USA, Australia)")

_index_locks = {}


def sanitize(name):
    """ROM name -> libretro thumbnail basename (no extension)."""
    return _ILLEGAL.sub("_", name).strip()


def loose_key(name):
    """Aggressively normalized key for index matching.

    Drops every parenthesised/bracketed tag group (region, revision, scene
    tags), folds the trailing-article form, and reduces to bare alphanumerics
    so 'Legend of Zelda, The (USA) [!]' and 'The Legend of Zelda (Europe)'
    collapse onto the same key.
    """
    s = _TAGS.sub("", name).strip().lower()
    m = re.match(r"^(.*),\s*(the|a|an)$", s)
    if m:
        s = f"{m.group(2)} {m.group(1)}"
    return _LOOSE.sub("", s)


def variants(stem):
    """Ordered candidate thumbnail names for a ROM stem, best guess first."""
    seen, out = set(), []

    def add(v):
        v = sanitize(v)
        if v and v not in seen:
            seen.add(v)
            out.append(v)

    add(stem)
    # article swap both directions — No-Intro uses "Name, The", plenty of
    # renamed sets use "The Name"
    m = re.match(r"^(.*?),\s*(The|A|An)(\s*[\(\[].*)?$", stem)
    if m:
        add(f"{m.group(2)} {m.group(1)}{m.group(3) or ''}")
    m = re.match(r"^(The|A|An)\s+(.*?)(\s*[\(\[].*)?$", stem)
    if m:
        add(f"{m.group(2)}, {m.group(1)}{m.group(3) or ''}")

    bare = _TAGS.sub("", stem).strip()
    if bare and bare != stem:
        add(bare)
        for region in COMMON_REGIONS:
            add(f"{bare} {region}")
    else:
        for region in COMMON_REGIONS:
            add(f"{stem} {region}")
    return out


def rom_stem(rom):
    """'Super Mario Bros. 3 (USA).nes' -> 'Super Mario Bros. 3 (USA)'."""
    return os.path.splitext(os.path.basename(rom))[0]


def _cache_path(system, kind, stem):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:180]
    return CACHE_DIR / system / kind / (safe + ".png")


def _thumb_url(platform, kind, name):
    return "{}/{}/{}/{}.png".format(
        THUMB_BASE,
        urllib.parse.quote(platform),
        KINDS[kind],
        urllib.parse.quote(name),
    )


async def _get(session, url):
    try:
        async with session.get(url) as r:
            if r.status != 200:
                return None
            body = await r.read()
            # A CDN error page is not art. Anything implausibly small is a
            # miss, not a cacheable image.
            return body if len(body) > 512 else None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None


# ------------------------------------------------------------------ index

def _index_path(platform, kind):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", platform)
    return CACHE_DIR / "_index" / f"{safe}.{kind}.json"


async def _load_index(session, platform, kind):
    """{loose_key: thumbnail_name} for one system, cached on disk.

    Parsed out of the CDN's autoindex listing rather than the GitHub API:
    the API is rate limited to 60 requests/hour unauthenticated, and a
    cabinet warming several systems would burn through that immediately.
    """
    path = _index_path(platform, kind)
    try:
        st = path.stat()
        if time.time() - st.st_mtime < INDEX_TTL:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass

    lock = _index_locks.setdefault(str(path), asyncio.Lock())
    async with lock:
        try:      # another waiter may have just built it
            st = path.stat()
            if time.time() - st.st_mtime < INDEX_TTL:
                return json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        url = "{}/{}/{}/".format(THUMB_BASE, urllib.parse.quote(platform),
                                 KINDS[kind])
        try:
            async with session.get(url) as r:
                if r.status != 200:
                    return {}
                html = await r.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
            return {}
        table = {}
        for href in re.findall(r'href="([^"?][^"]*\.png)"', html):
            name = urllib.parse.unquote(os.path.basename(href))[:-4]
            # first writer wins: listings are sorted, so this prefers the
            # earlier (usually USA/World) region for a colliding key
            table.setdefault(loose_key(name), name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(table))
        os.replace(tmp, path)
        return table


# ------------------------------------------------------------------ lookup

async def _fetch_steam(session, appid):
    for suffix in ("library_600x900_2x.jpg", "library_600x900.jpg",
                   "header.jpg"):
        body = await _get(session, f"{STEAM_BASE}/{appid}/{suffix}")
        if body:
            return body
    return None


async def art(system, rom, kind="box", session=None):
    """Bytes of the artwork for one game, or None.

    `system` is a /roms directory name; `rom` a filename or bare title. Steam
    entries pass the appid as `rom` and come from Steam's own CDN, which has
    portrait library art that libretro-thumbnails has no equivalent for.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    system = system.lower()
    stem = rom_stem(rom)
    cached = _cache_path(system, kind, stem)
    if cached.exists():
        return cached.read_bytes() or None
    miss = cached.with_suffix(".miss")
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_TTL:
        return None

    owns_session = session is None
    if owns_session:
        session = aiohttp.ClientSession(timeout=TIMEOUT)
    try:
        body = None
        if system == "steam" and stem.isdigit():
            body = await _fetch_steam(session, stem)
        else:
            platform = PLATFORMS.get(system)
            if platform:
                for name in variants(stem):
                    body = await _get(session, _thumb_url(platform, kind, name))
                    if body:
                        break
                if body is None:
                    table = await _load_index(session, platform, kind)
                    hit = table.get(loose_key(stem))
                    if hit:
                        body = await _get(session,
                                          _thumb_url(platform, kind, hit))
        if body is None:
            miss.parent.mkdir(parents=True, exist_ok=True)
            miss.write_bytes(b"")
            return None
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".part")
        tmp.write_bytes(body)
        os.replace(tmp, cached)
        try:
            miss.unlink()
        except FileNotFoundError:
            pass
        return body
    finally:
        if owns_session:
            await session.close()


async def warm(items, kind="box", concurrency=6):
    """Pre-fetch art for many games. Returns {'hit': n, 'miss': n}.

    Bounded concurrency on purpose: this runs over a whole library and the
    thumbnail CDN is a free service.
    """
    sem = asyncio.Semaphore(concurrency)
    counts = {"hit": 0, "miss": 0}

    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        async def one(system, rom):
            async with sem:
                got = await art(system, rom, kind, session=session)
            counts["hit" if got else "miss"] += 1

        await asyncio.gather(*(one(s, r) for s, r in items))
    return counts


def cache_stats():
    files = size = 0
    misses = 0
    if CACHE_DIR.is_dir():
        for p in CACHE_DIR.rglob("*"):
            if p.is_file():
                if p.suffix == ".miss":
                    misses += 1
                elif p.suffix == ".png":
                    files += 1
                    size += p.stat().st_size
    return {"cached": files, "bytes": size, "negative": misses,
            "dir": str(CACHE_DIR)}


def cached_path(system, rom, kind="box"):
    """Path of an already-cached image, or None. No network."""
    p = _cache_path(system.lower(), kind, rom_stem(rom))
    return p if p.exists() else None
