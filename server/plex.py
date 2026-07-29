#!/usr/bin/env python3
"""Plex adapter (spec §6.1, replacing the Jellyfin adapter).

Same four-function contract the rest of the server already consumes:
  items()          -> [{id, name, type, thumb}]  every show + movie
  episodes_of(id)  -> [rating_key, ...]          playable videos under a source
  image(id)        -> (bytes, content_type)      poster for the sources list
  stream_path(id)  -> "/path?..."                480p-capped transcode, proxied

Auth: a Plex Media Server on your own LAN answers its HTTP API unauthenticated
until you claim it, and keeps answering for local clients afterwards. If your
server needs a token (claimed + "require auth on local network"), set
PLEX_TOKEN and it is sent as X-Plex-Token on every request — that is the only
configuration difference between the sim and a real claimed server.

Bootstrap is idempotent: it creates Shows/Movies sections pointed at
PLEX_MEDIA_DIR if they are missing, so a fresh sim server and a
pre-configured real one both converge (OQ-7).
"""
import asyncio
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

PLEX_URL = os.environ.get("PLEX_URL", "http://plex:32400")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN") or None
PLEX_MEDIA_DIR = os.environ.get("PLEX_MEDIA_DIR", "/media")
# On an unclaimed/local server, tell Plex to accept our subnet without a
# token. Set PLEX_ALLOW_LAN=0 if your Plex is claimed and you use PLEX_TOKEN.
ALLOW_LAN = os.environ.get("PLEX_ALLOW_LAN", "1") != "0"
ALLOWED_NETWORKS = os.environ.get("PLEX_ALLOWED_NETWORKS", "0.0.0.0/0")
MAX_HEIGHT = int(os.environ.get("SCREENSAVER_MAX_HEIGHT", "480"))
MAX_WIDTH = int(os.environ.get("SCREENSAVER_MAX_WIDTH", "854"))
VIDEO_BITRATE = int(os.environ.get("SCREENSAVER_VIDEO_BITRATE", "1500000"))

# kept for API-compatibility with the old adapter's callers
JF_URL = PLEX_URL

_setup_lock = asyncio.Lock()
_bootstrapped = False

# Plex refuses /video/:/transcode/universal/start.m3u8 with a bare 400
# unless the client identifies its platform — Platform/Device are the
# minimum extra fields that make the transcoder answer.
HEADERS = {
    "X-Plex-Product": "arcaded",
    "X-Plex-Client-Identifier": "arcaded-cabinet",
    "X-Plex-Version": "1.0",
    "X-Plex-Platform": "Chrome",
    "X-Plex-Device": "Linux",
    "Accept": "application/xml",
}


def _url(_path, **params):
    """Build a path+query. The leading arg is underscore-named because Plex
    itself uses a query param called `path` (transcode target)."""
    if PLEX_TOKEN:
        params["X-Plex-Token"] = PLEX_TOKEN
    if params:
        sep = "&" if "?" in _path else "?"
        _path = f"{_path}{sep}{urllib.parse.urlencode(params)}"
    return _path


def _req(path, method="GET", timeout=30):
    """Returns the parsed XML root, or None for an empty body."""
    req = urllib.request.Request(PLEX_URL + path, method=method)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return ET.fromstring(body) if body.strip() else None


def _sections():
    root = _req(_url("/library/sections"))
    out = {}
    for d in (root or []):
        out[d.get("type")] = d.get("key")
    return out


def _bootstrap_sync():
    """Create Shows/Movies sections if absent. Safe to re-run."""
    # Let clients on other subnets (arcaded is not on Plex's own network in
    # the sim, and may not be in prod either) use the API without a token.
    # Set through Plex's own prefs API — hand-editing Preferences.xml
    # corrupts it and Plex then refuses to start.
    if not PLEX_TOKEN and ALLOW_LAN:
        try:
            _req(_url("/:/prefs", allowedNetworks=ALLOWED_NETWORKS),
                 method="PUT")
        except urllib.error.HTTPError:
            pass
    have = _sections()
    wanted = [
        ("show", "Shows", "tv.plex.agents.series", "Plex TV Series", "Shows"),
        ("movie", "Movies", "tv.plex.agents.movie", "Plex Movie", "Movies"),
    ]
    for stype, name, agent, scanner, subdir in wanted:
        if stype in have:
            continue
        loc = os.path.join(PLEX_MEDIA_DIR, subdir)
        if not os.path.isdir(loc):
            # the server may not share the media mount; let Plex decide
            loc = os.path.join(PLEX_MEDIA_DIR, subdir)
        try:
            _req(_url("/library/sections", name=name, type=stype, agent=agent,
                      scanner=scanner, language="en-US", location=loc),
                 method="POST")
        except urllib.error.HTTPError:
            pass   # already exists / racing another bootstrap
    # kick a scan so freshly-added files show up
    for key in _sections().values():
        try:
            _req(_url(f"/library/sections/{key}/refresh"))
        except urllib.error.HTTPError:
            pass


async def ensure_setup():
    global _bootstrapped
    async with _setup_lock:
        if _bootstrapped:
            return
        await asyncio.to_thread(_bootstrap_sync)
        _bootstrapped = True


async def token():
    """Compat shim: the old adapter exposed a token coroutine."""
    await ensure_setup()
    return PLEX_TOKEN or ""


def _all_items_sync():
    out = []
    for stype, key in _sections().items():
        if stype not in ("show", "movie"):
            continue
        root = _req(_url(f"/library/sections/{key}/all"))
        for d in (root or []):
            rk = d.get("ratingKey")
            if not rk:
                continue
            out.append({
                "id": rk,
                "name": d.get("title") or "(untitled)",
                "type": "Series" if stype == "show" else "Movie",
                "thumb": f"/thumb/{rk}" if d.get("thumb") else None,
            })
    out.sort(key=lambda i: (i["type"], i["name"]))
    return out


async def items():
    """All Series and Movies: [{id, name, type, thumb}]."""
    await ensure_setup()
    return await asyncio.to_thread(_all_items_sync)


def _episodes_sync(item_id):
    """Playable video rating keys under a source.

    A movie is itself playable. A show must be expanded to its episodes —
    /allLeaves returns every episode across seasons in one call.
    """
    meta = _req(_url(f"/library/metadata/{item_id}"))
    node = next(iter(meta), None) if meta is not None else None
    if node is None:
        return []
    if node.tag.lower() == "video" or node.get("type") == "movie":
        return [item_id]
    leaves = _req(_url(f"/library/metadata/{item_id}/allLeaves"))
    return [v.get("ratingKey") for v in (leaves or []) if v.get("ratingKey")]


async def episodes_of(item_id):
    await ensure_setup()
    return await asyncio.to_thread(_episodes_sync, item_id)


async def image(item_id, max_height=180):
    """Poster bytes + content type, or None when the item has no art."""
    def _fetch():
        meta = _req(_url(f"/library/metadata/{item_id}"))
        node = next(iter(meta), None) if meta is not None else None
        thumb = node.get("thumb") if node is not None else None
        if not thumb:
            return None
        req = urllib.request.Request(PLEX_URL + _url(thumb))
        for k, v in HEADERS.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read(), r.headers.get("Content-Type", "image/jpeg")
        except urllib.error.HTTPError:
            return None
    await ensure_setup()
    return await asyncio.to_thread(_fetch)


def _part_path_sync(video_id):
    """The on-disk file behind a video, as a Plex URL path.

    Part ids are per-file and change on rescan, so they must be resolved
    from metadata every time rather than cached or guessed.
    """
    meta = _req(_url(f"/library/metadata/{video_id}"))
    for video in (meta or []):
        for media in video:
            if media.tag != "Media":
                continue
            for part in media:
                if part.tag == "Part" and part.get("key"):
                    return part.get("key")
    return None


async def part_path(video_id):
    """Direct-play path for a video (no Plex transcode involved).

    We deliberately do NOT use /video/:/transcode/universal/*: it requires a
    per-session `decision` handshake, and even with that it fails on a
    server without internet egress because Plex downloads codecs from
    codecs.plex.tv on first transcode. arcaded runs its own ffmpeg instead
    (see media_proxy), which both removes that dependency and puts the
    tunnel bandwidth cap under our control.
    """
    await ensure_setup()
    p = await asyncio.to_thread(_part_path_sync, video_id)
    if not p:
        raise RuntimeError(f"no playable part for video {video_id}")
    return _url(p)


async def stream_path(video_id):
    """Back-compat alias: callers ask for a path, we hand back the part."""
    return await part_path(video_id)


async def _unused_transcode_path(video_id):
    """Kept for reference: the Plex-side transcode we cannot rely on."""
    await ensure_setup()
    # A distinct session per video: reusing one session id makes Plex tear
    # down the previous transcode when the playlist advances.
    return _url(
        "/video/:/transcode/universal/start.m3u8",
        **{
            "path": f"/library/metadata/{video_id}",
            "protocol": "hls",
            "maxVideoBitrate": int(VIDEO_BITRATE / 1000),   # Plex wants kbps
            "videoResolution": f"{MAX_WIDTH}x{MAX_HEIGHT}",
            "videoQuality": 60,
            "directPlay": 0,
            "directStream": 0,
            "fastSeek": 1,
            "mediaIndex": 0,
            "partIndex": 0,
            "session": f"arcade-ss-{video_id}",
            "X-Plex-Client-Identifier": HEADERS["X-Plex-Client-Identifier"],
            "X-Plex-Product": HEADERS["X-Plex-Product"],
            "X-Plex-Platform": HEADERS["X-Plex-Platform"],
            "X-Plex-Device": HEADERS["X-Plex-Device"],
        })
