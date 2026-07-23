#!/usr/bin/env python3
"""Jellyfin adapter (spec §6.1): enumerate libraries, mint 480p-capped
stream URLs. In sim, also bootstraps a fresh Jellyfin (startup wizard,
arcade/arcade user, Shows+Movies libraries) — idempotent, so a real
pre-configured Jellyfin just authenticates (OQ-7).
"""
import asyncio
import json
import os
import urllib.parse
import urllib.request

JF_URL = os.environ.get("JELLYFIN_URL", "http://jellyfin:8096")
JF_USER = os.environ.get("JELLYFIN_USER", "arcade")
JF_PASS = os.environ.get("JELLYFIN_PASS", "arcade")
MAX_HEIGHT = int(os.environ.get("SCREENSAVER_MAX_HEIGHT", "480"))
MAX_WIDTH = int(os.environ.get("SCREENSAVER_MAX_WIDTH", "854"))
VIDEO_BITRATE = int(os.environ.get("SCREENSAVER_VIDEO_BITRATE", "1500000"))

_token = os.environ.get("JELLYFIN_TOKEN") or None  # pre-provisioned API key
_user_id = None
_setup_lock = asyncio.Lock()
_stream_cache = {}   # video_id -> TranscodingUrl (one session per video)

AUTH_HEADER = ('MediaBrowser Client="arcaded", Device="server", '
               'DeviceId="arcaded", Version="1.0"')


def _req(method, path, body=None, token=None, timeout=30):
    url = JF_URL + path
    req = urllib.request.Request(url, method=method)
    req.add_header("X-Emby-Authorization",
                   AUTH_HEADER + (f', Token="{token}"' if token else ""))
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}


def _bootstrap_sync():
    """Complete the startup wizard + create libraries. Idempotent."""
    info = _req("GET", "/System/Info/Public")
    if not info.get("StartupWizardCompleted"):
        _req("POST", "/Startup/Configuration",
             {"UICulture": "en-US", "MetadataCountryCode": "US",
              "PreferredMetadataLanguage": "en"})
        _req("GET", "/Startup/User")
        _req("POST", "/Startup/User", {"Name": JF_USER, "Password": JF_PASS})
        _req("POST", "/Startup/Complete")

    auth = _req("POST", "/Users/AuthenticateByName",
                {"Username": JF_USER, "Pw": JF_PASS})
    token = auth["AccessToken"]

    existing = {v["Name"] for v in _req("GET", "/Library/VirtualFolders",
                                        token=token)}
    for name, ctype, path in (("Shows", "tvshows", "/media/Shows"),
                              ("Movies", "movies", "/media/Movies")):
        if name not in existing:
            q = urllib.parse.urlencode(
                {"name": name, "collectionType": ctype, "refreshLibrary": "true"})
            _req("POST", f"/Library/VirtualFolders?{q}",
                 {"LibraryOptions": {"PathInfos": [{"Path": path}]}},
                 token=token)
    return token


async def token():
    """With JELLYFIN_TOKEN set (real pre-configured server, OQ-7/A-15) we
    skip the wizard/user bootstrap entirely; otherwise bootstrap (sim)."""
    global _token, _user_id
    async with _setup_lock:
        if _token is None:
            _token = await asyncio.to_thread(_bootstrap_sync)
        if _user_id is None:
            me = await asyncio.to_thread(_req, "GET", "/Users/Me", None, _token)
            _user_id = me["Id"]
    return _token


async def items():
    """All Series and Movies: [{id, name, type}]."""
    t = await token()
    data = await asyncio.to_thread(
        _req, "GET",
        "/Items?IncludeItemTypes=Series,Movie&Recursive=true", None, t)
    return [{"id": i["Id"], "name": i["Name"], "type": i["Type"]}
            for i in data.get("Items", [])]


async def episodes_of(item_id):
    """Playable video item ids under a source. A Movie is itself playable —
    and must NOT be ParentId-queried: Jellyfin ignores a non-folder ParentId
    and would return the entire library."""
    t = await token()
    meta = await asyncio.to_thread(_req, "GET", f"/Items?ids={item_id}", None, t)
    items_meta = meta.get("Items", [])
    if not items_meta or items_meta[0].get("Type") != "Series":
        return [item_id]
    data = await asyncio.to_thread(
        _req, "GET",
        f"/Items?ParentId={item_id}&IncludeItemTypes=Episode&Recursive=true",
        None, t)
    vids = [i["Id"] for i in data.get("Items", [])]
    return vids or [item_id]


async def stream_path(video_id):
    """Server-relative transcoded stream path (progressive TS, 480p cap).

    Uses PlaybackInfo with a device profile: empty DirectPlayProfiles force
    a transcode session, and BOTH Height and Width codec conditions are
    load-bearing — with only Height, Jellyfin's scale filter caps width at
    the source and emits the original resolution (verified empirically:
    stream.ts?maxHeight=... alone is ignored entirely).

    Cached per video: each PlaybackInfo mints a new transcode session (a
    near-realtime ffmpeg on the server) — verified: 3 uncached fetches of
    one video ran 3 concurrent ffmpegs."""
    if video_id in _stream_cache:
        return _stream_cache[video_id]
    t = await token()
    body = {"DeviceProfile": {
        "MaxStreamingBitrate": VIDEO_BITRATE,
        "DirectPlayProfiles": [],
        "TranscodingProfiles": [{
            "Container": "ts", "Type": "Video", "VideoCodec": "h264",
            "AudioCodec": "aac", "Protocol": "http", "Context": "Streaming"}],
        "CodecProfiles": [{"Type": "Video", "Conditions": [
            {"Condition": "LessThanEqual", "Property": "Height",
             "Value": str(MAX_HEIGHT), "IsRequired": True},
            {"Condition": "LessThanEqual", "Property": "Width",
             "Value": str(MAX_WIDTH), "IsRequired": True}]}]}}
    q = urllib.parse.urlencode({
        "UserId": _user_id, "IsPlayback": "true",
        "AutoOpenLiveStream": "true", "MaxStreamingBitrate": VIDEO_BITRATE})
    info = await asyncio.to_thread(
        _req, "POST", f"/Items/{video_id}/PlaybackInfo?{q}", body, t)
    turl = info["MediaSources"][0].get("TranscodingUrl")
    if not turl:
        raise RuntimeError(f"no TranscodingUrl for {video_id}")
    _stream_cache[video_id] = turl
    return turl
