"""YouTube commercial sources and a bounded, 480p server-side clip cache."""
import asyncio
import json
import os
import pathlib
import random
import re
import signal
import tempfile
import time
import urllib.parse

DEFAULT_SOURCES = [
    {"url": "https://www.youtube.com/playlist?list=PLN7svyn8WVPDqwD565BkG74pfArzagzK6", "enabled": True},
    {"url": "https://www.youtube.com/playlist?list=PLWm34Sbaq72OFR4BsgUSMkDzn5sXlNcb9", "enabled": True},
    {"url": "https://www.youtube.com/watch?v=g5WlcjXiZZk", "enabled": True},
]
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
YTDLP = os.environ.get("YTDLP_BIN", "yt-dlp")
CACHE_DIR = pathlib.Path(os.environ.get("COMMERCIALS_CACHE", "/var/cache/arcade-commercials"))
CACHE_BYTES = int(os.environ.get("COMMERCIALS_CACHE_BYTES", str(2 * 1024**3)))
_catalog = {}
_locks = {}
_download_slots = asyncio.Semaphore(2)


def canonical_url(url):
    if not isinstance(url, str):
        raise ValueError("commercial source must be a YouTube video or playlist URL")
    p = urllib.parse.urlsplit(url.strip())
    q = urllib.parse.parse_qs(p.query)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or host not in (
            "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"):
        raise ValueError("commercial source must be a YouTube video or playlist URL")
    if host != "youtu.be" and p.path in ("/playlist", "/watch") and q.get("list"):
        pid = q["list"][0]
        if re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            return "https://www.youtube.com/playlist?list=" + pid
    vid = p.path.strip("/") if host == "youtu.be" else (
        q.get("v", [""])[0] if p.path == "/watch" else
        p.path.split("/")[-1] if p.path.startswith(("/shorts/", "/embed/")) else "")
    if VIDEO_ID.fullmatch(vid):
        return "https://www.youtube.com/watch?v=" + vid
    raise ValueError("commercial source must be a YouTube video or playlist URL")


def normalize_sources(sources):
    if not isinstance(sources, list):
        raise ValueError("commercial_sources must be a list")
    out = []
    seen = set()
    for s in sources:
        if not isinstance(s, dict) or type(s.get("enabled")) is not bool:
            raise ValueError("each commercial source needs a url and enabled boolean")
        url = canonical_url(s.get("url"))
        if url not in seen:
            out.append({"url": url, "enabled": s["enabled"]})
            seen.add(url)
    return out


async def run(*argv, timeout=60):
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            os.killpg(proc.pid, signal.SIGKILL)
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError(stderr.decode(errors="replace")[-1200:].strip() or "media command failed")
    return stdout.decode()


async def videos(url):
    # A single video needs no remote enumeration; playlists refresh hourly.
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    if "v" in q:
        return q["v"]
    async with _locks.setdefault(("catalog", url), asyncio.Lock()):
        previous = _catalog.get(url)
        if previous and previous[0] > time.monotonic():
            return previous[1]
        try:
            raw = await run(YTDLP, "--ignore-config", "--js-runtimes", "node", "--flat-playlist", "--dump-single-json",
                            "--skip-download", "--ignore-errors", "--socket-timeout", "10",
                            "--retries", "1", "--", url, timeout=45)
            entries = json.loads(raw).get("entries") or []
            ids = list(dict.fromkeys(e["id"] for e in entries if e and
                       VIDEO_ID.fullmatch(str(e.get("id", ""))) and
                       e.get("availability") not in ("private", "premium_only", "subscriber_only")
                       and e.get("title") not in ("[Deleted video]", "[Private video]")))
            _catalog[url] = (time.monotonic() + 3600, ids)
            return ids
        except (OSError, RuntimeError, ValueError, asyncio.TimeoutError):
            # Retain a working catalog through transient YouTube outages.
            _catalog[url] = (time.monotonic() + 60, previous[1] if previous else [])
            raise


async def pool(sources, log):
    enabled = [s["url"] for s in sources if s["enabled"]]
    results = await asyncio.gather(*(videos(url) for url in enabled), return_exceptions=True)
    ids = []
    for url, result in zip(enabled, results):
        if isinstance(result, Exception):
            log(f"screensaver commercials: {url}: {result}")
        else:
            ids.extend(result)
    return list(dict.fromkeys(ids))


def interleave(episodes, ids, count, rng=None):
    """The final break also separates the last episode from the loop's first."""
    if not count or not ids or not episodes:
        return list(episodes)
    rng = rng or random
    bag = []
    out = []
    for episode in episodes:
        out.append(episode)
        for _ in range(count):
            if not bag:
                bag = list(ids)
                rng.shuffle(bag)
            out.append("/commercials/" + bag.pop())
    return out


async def clip(vid, encoder):
    if not VIDEO_ID.fullmatch(vid):
        raise ValueError("invalid commercial video id")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / (vid + ".mkv")
    async with _locks.setdefault(("clip", vid), asyncio.Lock()):
        if target.is_file():
            target.touch()
            return target
        async with _download_slots:
            with tempfile.TemporaryDirectory(prefix="download-", dir=CACHE_DIR) as tmp:
                await run(YTDLP, "--ignore-config", "--js-runtimes", "node", "--no-playlist", "--socket-timeout", "10",
                          "--retries", "1", "--no-progress", "-f", "bv*[height<=480]+ba/b[height<=480]/b",
                          "--ffmpeg-location", encoder("")[0],
                          "--merge-output-format", "mkv", "-o", tmp + "/source.%(ext)s",
                          "--", "https://www.youtube.com/watch?v=" + vid, timeout=180)
                sources = [p for p in pathlib.Path(tmp).glob("source.*")
                           if re.fullmatch(r"source\.(mkv|webm|mp4|flv|3gp)", p.name)]
                if not sources:
                    raise RuntimeError("YouTube returned no playable commercial")
                source = sources[0]
                output = pathlib.Path(tmp) / "clip.mkv"
                # The shared encoder enforces the same 480p/tunnel bitrate as episodes.
                await run(*encoder(str(source))[:-3], "-map", "0:V:0", "-map", "0:a:0?",
                          "-y", str(output), timeout=600)
                if output.stat().st_size > CACHE_BYTES:
                    raise RuntimeError("Commercial exceeds the clip cache size limit")
                output.replace(target)
            total = sum(p.stat().st_size for p in CACHE_DIR.glob("*.mkv"))
            for old in sorted(CACHE_DIR.glob("*.mkv"), key=lambda p: p.stat().st_mtime):
                if total <= CACHE_BYTES:
                    break
                if old != target:
                    total -= old.stat().st_size
                    old.unlink(missing_ok=True)
        return target
