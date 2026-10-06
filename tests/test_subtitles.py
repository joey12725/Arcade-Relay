#!/usr/bin/env python3
"""Screensaver subtitle choice (server/subtitles.py) and the ffmpeg argv
that carries it (arcaded.media_argv).

The fixtures are the <Stream> attribute shapes Plex reports. The last test
runs the real argv through ffmpeg on a synthetic anime-style file and checks
what comes out the other end, because a plausible argv that ffmpeg reads
differently is the failure that matters.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import subtitles  # noqa: E402


def sub(index=None, codec="ass", lang="eng", title=None, key=None, **flags):
    s = {"streamType": "3", "codec": codec}
    if index is not None:
        s["index"] = str(index)
    if lang:
        s["languageCode"] = lang
    if title:
        s["title"] = title
    if key:
        s["key"] = key
    s.update({k: "1" for k, v in flags.items() if v})
    return s


def audio(index, lang, channels, default=False):
    s = {"streamType": "2", "index": str(index), "codec": "aac",
         "languageCode": lang, "channels": str(channels)}
    if default:
        s["default"] = "1"
    return s


VIDEO = {"streamType": "1", "index": "0", "codec": "h264"}


def test_english_detection():
    assert subtitles.is_english(sub(2, lang="eng"))
    assert subtitles.is_english({"languageTag": "en-US"})
    assert subtitles.is_english({"language": "English"})
    assert subtitles.is_english(sub(2, lang=None, title="English (Full)"))
    assert subtitles.is_english(sub(2, lang="und", title="Eng"))
    assert subtitles.is_english({"languageTag": "eng"})      # OpenSubtitles
    assert subtitles.is_english({"language": "English (Weeb)"})
    assert not subtitles.is_english(sub(2, lang=None))
    assert not subtitles.is_english(sub(2, lang="jpn"))


def test_mistagged_english_is_rescued_by_name():
    # Initial D / My Hero Academia S1: English tracks tagged jpn
    assert subtitles.is_english(sub(4, lang="jpn", title="English [Signs/Songs]"))
    assert subtitles.is_english(sub(4, lang="jpn", title="English"))
    # Kill la Kill: the full translation, tagged jpn, named only "Subtitles"
    assert subtitles.is_english(sub(4, lang="jpn", title="Subtitles [FFF]"))
    # ...while genuine Japanese tracks stay out
    for t in ("SDH", "Dubtitle", "Japanese (SDH)", None,
              "Japanese Full Subtitles"):
        assert not subtitles.is_english(sub(4, codec="srt", lang="jpn", title=t)), t
    assert not subtitles.is_english(sub(4, lang="spa", title="Full Subtitles"))


def test_coverage():
    assert subtitles.coverage(sub(2, title="Full Subtitles")) == subtitles.FULL
    assert subtitles.coverage(sub(2)) == subtitles.FULL
    assert subtitles.coverage(sub(2, title="Signs & Songs")) == subtitles.SIGNS
    assert subtitles.coverage(sub(2, forced=True)) == subtitles.SIGNS
    assert subtitles.coverage(sub(2, title="Full [Signs/Songs]")) == subtitles.FULL
    assert subtitles.coverage(sub(2, title="SDH")) == subtitles.ALT
    assert subtitles.coverage(sub(2, hearingImpaired=True)) == subtitles.ALT
    assert subtitles.coverage(sub(2, title="English (Dubtitle)")) == subtitles.ALT
    assert subtitles.coverage(sub(2, title="Director Commentary")) == 0
    # "Subtitles" inside a word is not a titles-only track
    assert subtitles.coverage(sub(2, title="English Subtitles")) == subtitles.FULL
    assert subtitles.coverage(sub(2, title="Titles")) == subtitles.SIGNS
    for t in ("S&S", "English Forced [CR]", "Forced (Signs only)",
              "Sign and Songs", "ASS / Forced", "English[Signs]"):
        assert subtitles.coverage(sub(2, title=t)) == subtitles.SIGNS, t
    for t in ("Dialouge [ENG]", "ASS / Main", "Full Subs (Official Subs)",
              "Dialog - ENG (ASS)", "English [Full]"):
        assert subtitles.coverage(sub(2, title=t)) == subtitles.FULL, t
    for t in ("[ASS] [FOR DUB]", "English Closed Captions [CR]",
              "Dubtitle (SDH)", "English[CC]"):
        assert subtitles.coverage(sub(2, title=t)) == subtitles.ALT, t


def test_unnamed_bitmap_track_judged_by_bitrate():
    light = dict(sub(3, codec="pgs"), bitrate="6")
    heavy = dict(sub(4, codec="pgs"), bitrate="41")
    assert subtitles.coverage(light) == subtitles.SIGNS
    assert subtitles.coverage(heavy) == subtitles.FULL
    assert subtitles.pick([light, heavy]) is heavy
    # a name still wins over the bitrate
    assert subtitles.coverage(dict(sub(3, codec="pgs", title="Dialogue"),
                                   bitrate="6")) == subtitles.FULL


def test_forced_flag_needs_an_unforced_sibling():
    # SENPAI/KAWAII: the one English track is the full translation, flagged
    # forced+default — a flag on every track says nothing
    lone = sub(3, title="English", forced=True, default=True)
    assert subtitles.pick([lone]) is lone
    v0 = {"streams": [VIDEO, audio(1, "jpn", 2, default=True), lone]}
    dub = {"streams": [VIDEO, audio(1, "eng", 2, default=True),
                       sub(3, title="English (US)")]}
    assert subtitles.choose([v0, dub])[0] is v0
    # next to an unforced track the flag is evidence again (Daemons/Judas)
    assert subtitles.pick([sub(3, title="English", forced=True, default=True),
                           sub(4, title="English")])["index"] == "4"


def test_no_switch_to_an_unproven_dub_only_track():
    signs = {"streams": [VIDEO, audio(1, "eng", 2, default=True),
                         sub(2, title="English Signs", default=True)]}
    # dub-only alternative whose only track is a nameless "English": on
    # these releases that is the signs track as often as not
    sdtv = {"streams": [VIDEO, audio(1, "eng", 2, default=True),
                        sub(2, title="English")]}
    assert subtitles.choose([signs, sdtv])[0] is signs
    # ...but proof is enough: a name that says so, a signs sibling, or the
    # original-language audio
    named = {"streams": [VIDEO, audio(1, "eng", 2), sub(2, title="Full Subtitles")]}
    pair = {"streams": [VIDEO, audio(1, "eng", 2), sub(2, title="English"),
                        sub(3, title="English [Signs]")]}
    jpn = {"streams": [VIDEO, audio(1, "jpn", 2), sub(2, title="English")]}
    for alt in (named, pair, jpn):
        assert subtitles.choose([signs, alt])[0] is alt
    # a multi-dub release without the original audio is still a dub release
    multi = {"streams": [VIDEO, audio(1, "eng", 2), audio(2, "spa", 2),
                         audio(3, "fra", 2), sub(6, title="English (US)")]}
    assert subtitles.choose([signs, multi])[0] is signs
    # the first version needs no proof
    assert subtitles.choose([sdtv, signs])[0] is sdtv


def test_switched_version_keeps_the_audio_language():
    nf = [VIDEO, audio(1, "por", 6, default=True), audio(2, "eng", 6),
          audio(3, "jpn", 2)]
    assert subtitles.pick_audio(nf)["index"] == "1"            # ffmpeg's own
    assert subtitles.pick_audio(nf, "eng")["index"] == "2"
    assert subtitles.pick_audio(nf, "ENG")["index"] == "2"
    assert subtitles.pick_audio(nf, "kor")["index"] == "1"     # not there


def test_plain_beats_honorifics():
    assert subtitles.pick([sub(3, title="Dialogue [Kaizoku] (Honorifics)",
                                lang="enm", default=True),
                           sub(4, title="Dialogue [Kaizoku]")])["index"] == "4"
    assert subtitles.honorifics(sub(3, title="Honorific"))
    assert not subtitles.honorifics(sub(3, title="Good Job! Media (No Honorifics)"))


def test_choose_version():
    bd = {"part": "/bd", "streams": [VIDEO, sub(3, codec="pgs"), sub(4, codec="pgs")]}
    dvd = {"part": "/dvd", "streams": [VIDEO, sub(2, title="[ASS]", default=True)]}
    good = {"part": "/good", "streams": [VIDEO, sub(3, title="Full Subtitles")]}
    bare = {"part": "/bare", "streams": []}                 # not analysed yet
    # first version wins unless another has a better TIER (coverage, text)
    assert subtitles.choose([good, dvd])[0] is good
    assert subtitles.choose([bd, dvd])[0] is dvd            # PGS -> ASS
    assert subtitles.choose([bd, bare])[0] is bd
    assert subtitles.choose([bare, bd])[0] is bd
    v, s = subtitles.choose([bare])
    assert v is bare and s is None
    signs = {"part": "/s", "streams": [VIDEO, sub(2, title="English Signs")]}
    assert subtitles.choose([signs, good])[0] is good


def test_typical_fansub_release_gets_full_dialogue():
    streams = [VIDEO, audio(1, "jpn", 2, default=True),
               sub(2, title="Signs & Songs", default=True, forced=True),
               sub(3, title="Full Subtitles"),
               sub(4, codec="srt", lang="jpn")]
    assert subtitles.pick(streams)["index"] == "3"


def test_text_beats_bitmap_and_ass_beats_srt():
    assert subtitles.pick([sub(2, codec="pgs", default=True),
                           sub(3, codec="srt")])["index"] == "3"
    assert subtitles.pick([sub(2, codec="srt", default=True),
                           sub(3, codec="ass")])["index"] == "3"
    # a bitmap track is still better than nothing
    assert subtitles.pick([sub(2, codec="pgs")])["index"] == "2"


def test_full_dialogue_beats_format():
    # an SDH/plain track that covers dialogue beats a styled signs-only one
    assert subtitles.pick([sub(2, title="Signs & Songs"),
                           sub(3, codec="pgs", title="SDH")])["index"] == "3"


# Track sets copied from the real library (census of the enabled sources).
def test_library_cases():
    # Panty & Stocking: the DEFAULT track is the (misspelled) titles track
    assert subtitles.pick([sub(3, title="Tittles", default=True),
                           sub(4, title="English Subtitles")])["index"] == "4"
    # Sakamoto Days (Netflix rip): forced / SDH / plain / dubtitle, all SRT
    assert subtitles.pick([sub(3, codec="srt", title="English (Forced)"),
                           sub(4, codec="srt", title="English (SDH)"),
                           sub(6, codec="srt", title="English (Dubtitle)"),
                           sub(5, codec="srt", title="English")])["index"] == "5"
    # Crunchyroll multi-sub: Forced / untitled default / CC
    assert subtitles.pick([sub(3, title="Forced", forced=True),
                           sub(4, default=True),
                           sub(5, codec="srt", title="CC"),
                           sub(6, lang="spa", default=True)])["index"] == "4"
    # BD remux: PGS signs flagged default, PGS dialogue not
    assert subtitles.pick([sub(3, codec="pgs", title="Signs", default=True),
                           sub(4, codec="pgs", title="Dialogue")])["index"] == "4"
    # Promare: the embedded track is forced signs; the sidecar is full
    side = sub(None, codec="srt", key="/library/streams/5")
    assert subtitles.pick([sub(2, forced=True, default=True), side]) is side
    # Made in Abyss: honorifics track borrows "enm"; the plain one is default
    assert subtitles.is_english(sub(3, lang="enm", title="Honorifics"))
    assert subtitles.pick([sub(2, default=True),
                           sub(3, lang="enm", title="Honorifics")])["index"] == "2"
    # a name that says "full" beats a nameless default in the same format
    assert subtitles.pick([sub(2, default=True),
                           sub(3, title="Full Subtitles")])["index"] == "3"


def test_signs_only_is_a_last_resort_and_commentary_never():
    assert subtitles.pick([sub(2, title="Signs", forced=True)])["index"] == "2"
    assert subtitles.pick([sub(2, title="Commentary")]) is None


def test_default_flag_then_file_order_break_ties():
    assert subtitles.pick([sub(2), sub(3, default=True)])["index"] == "3"
    assert subtitles.pick([sub(2), sub(3)])["index"] == "2"


def test_no_english_means_no_track():
    assert subtitles.pick([VIDEO, sub(2, lang="jpn"), sub(3, lang="spa")]) is None
    assert subtitles.pick([VIDEO, sub(2, lang=None)]) is None
    assert subtitles.pick([VIDEO]) is None


def test_unmuxable_tracks_are_skipped():
    cc = sub(None, codec="eia_608", embeddedInVideo=True)
    assert subtitles.pick([cc]) is None
    lone_idx = sub(None, codec="vobsub", key="/library/streams/9")
    assert subtitles.pick([lone_idx]) is None
    assert subtitles.pick([sub(2, codec="xsub")]) is None


def test_sidecar():
    side = sub(None, codec="srt", key="/library/streams/77")
    assert subtitles.is_external(side)
    assert subtitles.pick([VIDEO, side]) is side
    # equal otherwise, the embedded track wins (no extra fetch)
    assert subtitles.pick([side, sub(2, codec="srt")])["index"] == "2"


def test_audio_replays_ffmpeg_choice():
    # default disposition outranks channel count...
    assert subtitles.pick_audio([audio(1, "jpn", 2, default=True),
                                 audio(2, "eng", 6)])["index"] == "1"
    # ...then most channels, then the first in the file
    assert subtitles.pick_audio([audio(1, "jpn", 2),
                                 audio(2, "eng", 6)])["index"] == "2"
    assert subtitles.pick_audio([audio(1, "jpn", 2),
                                 audio(2, "eng", 2)])["index"] == "1"
    assert subtitles.pick_audio([VIDEO]) is None


def test_sidecar_charsets_become_utf8():
    text = "1\n00:00:01,000 --> 00:00:02,000\n\u266a Café — naïve\n"
    want = text.encode("utf-8")
    assert subtitles.to_utf8(want) == want
    assert subtitles.to_utf8(b"\xef\xbb\xbf" + want) == want
    assert subtitles.to_utf8(text.encode("utf-16")) == want      # BOM'd
    latin = "Café — naïve".encode("cp1252")
    assert subtitles.to_utf8(latin) == "Café — naïve".encode("utf-8")


def test_codec_args():
    assert subtitles.codec_args(sub(2, codec="ass")) == ["-c:s", "copy"]
    assert subtitles.codec_args(sub(2, codec="pgs")) == ["-c:s", "copy"]
    assert subtitles.codec_args(sub(2, codec="mov_text")) == ["-c:s", "subrip"]


def _arcaded():
    try:
        import arcaded
    except ImportError as e:      # aiohttp/yaml absent: unit tests still run
        print(f"  skip arcaded tests: {e}")
        return None
    return arcaded


def test_argv_without_a_pick_strips_every_subtitle():
    a = _arcaded()
    if a is None:
        return
    argv = a.media_argv("http://plex/x.mkv")
    assert "-sn" in argv and "-map" not in argv


def test_argv_maps_embedded_pick_and_the_ffmpeg_audio():
    a = _arcaded()
    if a is None:
        return
    streams = [VIDEO, audio(1, "jpn", 2, default=True), audio(2, "eng", 6),
               sub(3, title="Full Subtitles")]
    argv = a.media_argv("http://plex/x.mkv", streams, subtitles.pick(streams))
    maps = [argv[i + 1] for i, v in enumerate(argv) if v == "-map"]
    assert maps == ["0:V:0", "0:1", "0:3"], maps
    assert "-sn" not in argv
    assert argv[argv.index("-c:s") + 1] == "copy"
    # flags cleared: the cabinet selects by language, and an old cabinet
    # (no --slang) must not auto-show a track nobody turned on
    assert argv[argv.index("-disposition:s:0") + 1] == "0"
    assert argv[argv.index("-max_interleave_delta") + 1] == "1000000"


def test_argv_sidecar_is_a_second_input():
    a = _arcaded()
    if a is None:
        return
    side = sub(None, codec="srt", key="/library/streams/77")
    argv = a.media_argv("http://plex/x.mkv", [VIDEO, side], side, "/tmp/s.srt")
    i = argv.index("/tmp/s.srt")
    # no forced -f: a wrong one gives a silently empty track
    assert argv[i - 1] == "-i" and argv[i - 3] != "-f", argv
    maps = [argv[j + 1] for j, v in enumerate(argv) if v == "-map"]
    assert maps == ["0:V:0", "0:a:0?", "1:0"], maps


def _ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name:stream_tags=language,title"
         ":stream_disposition=default,forced", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


def test_ffmpeg_end_to_end():
    """The argv, run for real: one subtitle track out, the right one, and
    the audio ffmpeg would have picked on its own."""
    a = _arcaded()
    if a is None or not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("  skip ffmpeg end-to-end: no ffmpeg/arcaded")
        return
    with tempfile.TemporaryDirectory() as d:
        def ass(name, text):
            p = os.path.join(d, name)
            with open(p, "w") as f:
                f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\n"
                        "PlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, "
                        "Fontsize, PrimaryColour, Alignment\nStyle: Default,Arial,"
                        "48,&H00FFFFFF,2\n\n[Events]\nFormat: Layer, Start, End, "
                        "Style, Text\n"
                        f"Dialogue: 0,0:00:00.20,0:00:01.80,Default,{text}\n")
            return p
        srt = os.path.join(d, "jpn.srt")
        with open(srt, "w") as f:
            f.write("1\n00:00:00,200 --> 00:00:01,800\nこんにちは\n")
        src = os.path.join(d, "src.mkv")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=2",
             "-f", "lavfi", "-i", "sine=f=440:d=2",
             "-f", "lavfi", "-i", "sine=f=880:d=2",
             "-i", ass("signs.ass", "SIGN"), "-i", ass("full.ass", "Hello"),
             "-i", srt,
             "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map", "4",
             "-map", "5", "-c:v", "libx264", "-c:a", "aac", "-c:s", "copy",
             "-ac:a:1", "6",
             "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
             "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=Signs & Songs",
             "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=Full Subtitles",
             "-metadata:s:s:2", "language=jpn",
             "-disposition:a:0", "default", "-disposition:a:1", "0",
             "-disposition:s:0", "default+forced", "-disposition:s:1", "0",
             "-disposition:s:2", "0", src],
            check=True)
        # what Plex would report for that file
        streams = [VIDEO, audio(1, "jpn", 2, default=True), audio(2, "eng", 6),
                   sub(3, title="Signs & Songs", default=True, forced=True),
                   sub(4, title="Full Subtitles"), sub(5, codec="srt", lang="jpn")]
        pick = subtitles.pick(streams)
        out = os.path.join(d, "out.mkv")
        for argv, path in ((a.media_argv(src, streams, pick), out),
                           (a.media_argv(src), out + ".nosub.mkv")):
            argv = [("ffmpeg" if v == a.FFMPEG else v) for v in argv]
            with open(path, "wb") as f:
                subprocess.run(argv, stdout=f, check=True, timeout=120)
        got = _ffprobe(out)
        subs = [s for s in got if s["codec_type"] == "subtitle"]
        auds = [s for s in got if s["codec_type"] == "audio"]
        assert len(subs) == 1, got
        assert subs[0]["codec_name"] == "ass"
        assert subs[0]["tags"].get("title") == "Full Subtitles"
        assert subs[0]["tags"].get("language") == "eng"
        # the source's default+forced flags are cleared, never copied
        assert subs[0]["disposition"] == {"default": 0, "forced": 0}
        assert len(auds) == 1 and auds[0]["tags"].get("language") == "jpn"
        nosub = _ffprobe(out + ".nosub.mkv")
        assert not [s for s in nosub if s["codec_type"] == "subtitle"], nosub
        assert [s["tags"].get("language") for s in nosub
                if s["codec_type"] == "audio"] == ["jpn"]


def test_media_proxy_end_to_end():
    """The handler itself, against a stubbed Plex: the subtitle path, the
    fall-back when ffmpeg refuses the mapping, and a cp1252 sidecar."""
    a = _arcaded()
    if a is None or not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("  skip media_proxy end-to-end: no ffmpeg/arcaded")
        return
    import asyncio
    import glob
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src.mkv")
        srt = os.path.join(d, "eng.srt")
        with open(srt, "w") as f:
            f.write("1\n00:00:00,200 --> 00:00:01,800\nHello\n")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=2",
             "-f", "lavfi", "-i", "sine=f=440:d=2", "-i", srt,
             "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264",
             "-c:a", "aac", "-c:s", "copy", "-metadata:s:a:0", "language=eng",
             "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=Signs",
             src], check=True)
        src2 = os.path.join(d, "src2.mkv")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=2",
             "-f", "lavfi", "-i", "sine=f=440:d=2",
             "-f", "lavfi", "-i", "sine=f=880:d=2", "-i", srt,
             "-map", "0", "-map", "1", "-map", "2", "-map", "3",
             "-c:v", "libx264", "-c:a", "aac", "-c:s", "copy",
             "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
             "-metadata:s:s:0", "language=eng", src2], check=True)
        cp1252 = "1\n00:00:00,200 --> 00:00:01,800\nCafé — naïve\n".encode("cp1252")
        cases = {
            "embedded": [VIDEO, audio(1, "eng", 1, default=True),
                         sub(2, codec="srt")],
            # Plex says index 9: the -map fails, the episode must still play
            "broken": [VIDEO, audio(1, "eng", 1, default=True),
                       sub(9, codec="srt")],
            "sidecar": [VIDEO, audio(1, "eng", 1, default=True),
                        sub(None, codec="srt", key="/library/streams/1")],
        }

        async def versions(video_id):
            if video_id == "switch":
                # v0: English-audio file with signs only; v1: a full track,
                # but the file defaults to another audio language
                return [{"part": src, "streams": [
                            VIDEO, audio(1, "eng", 1, default=True),
                            sub(2, codec="srt", title="Signs")]},
                        {"part": src2, "streams": [
                            VIDEO, audio(1, "jpn", 1, default=True),
                            audio(2, "eng", 1), sub(3, codec="srt")]}]
            return [{"part": src, "streams": cases[video_id]}]

        async def fetch(key, timeout=20):
            return cp1252

        setting = {"subtitles": True}
        saved = (a.plex.versions, a.plex.fetch, a.plex.PLEX_URL,
                 a.load_screensaver)
        a.plex.versions, a.plex.fetch, a.plex.PLEX_URL = versions, fetch, ""
        a.load_screensaver = lambda: dict(setting)
        before = set(glob.glob(os.path.join(tempfile.gettempdir(), "arcade-sub-*")))
        app = web.Application()
        app.router.add_get("/media/{video_id}", a.media_proxy)
        got = {}

        async def run():
            async with TestClient(TestServer(app)) as client:
                for name, on in [(n, True) for n in cases] + [
                        ("switch", True), ("switch", False)]:
                    setting["subtitles"] = on
                    r = await client.get(f"/media/{name}")
                    assert r.status == 200, (name, r.status)
                    key = f"{name}-{'on' if on else 'off'}"
                    path = os.path.join(d, f"{key}.mkv")
                    with open(path, "wb") as f:
                        f.write(await r.read())
                    got[name if name in cases else key] = path
        try:
            asyncio.run(run())
        finally:
            (a.plex.versions, a.plex.fetch, a.plex.PLEX_URL,
             a.load_screensaver) = saved

        def kinds(path):
            return [s["codec_type"] for s in _ffprobe(path)]
        assert kinds(got["embedded"]) == ["video", "audio", "subtitle"]
        assert kinds(got["broken"]) == ["video", "audio"]
        assert kinds(got["sidecar"]) == ["video", "audio", "subtitle"]
        text = subprocess.run(["ffmpeg", "-v", "error", "-i", got["sidecar"],
                               "-map", "0:s:0", "-f", "srt", "-"],
                              capture_output=True, check=True).stdout
        assert "Café — naïve".encode("utf-8") in text, text
        after = set(glob.glob(os.path.join(tempfile.gettempdir(), "arcade-sub-*")))
        assert after <= before, "sidecar temp file left behind"

        def tracks(path):
            return [(t["codec_type"], t.get("tags", {}).get("language"),
                     t.get("tags", {}).get("title"))
                    for t in _ffprobe(path) if t["codec_type"] != "video"]
        # subtitles on: the better-subtitled version, in the ORIGINAL's
        # audio language (eng), not the new file's default (jpn)
        assert tracks(got["switch-on"]) == [("audio", "eng", None),
                                            ("subtitle", "eng", None)]
        # subtitles off: the original file, still carrying its own track so
        # turning them on shows it at once
        assert tracks(got["switch-off"]) == [("audio", "eng", None),
                                             ("subtitle", "eng", "Signs")]


def test_cabinet_visibility():
    """cabinet/screensaver.py: --slang selects, visibility switches, and
    brightness 0 hides (mpv draws subtitles after the brightness tint)."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cabinet"))
    import screensaver
    args = screensaver._sub_args(True)
    assert "--slang=eng,en" in args and "--sub-visibility=yes" in args
    assert not any(a.startswith("--sid") for a in args)   # see _sub_args
    sent = []
    saved = screensaver.ipc
    screensaver.ipc = lambda cmd, timeout=2.0: sent.append(cmd) or {}
    try:
        screensaver._subs.update(on=False, brightness=80)
        screensaver.set_subtitles(True)
        assert sent[-1] == ["set_property", "sub-visibility", True]
        screensaver.set_brightness(0)
        assert sent[-1] == ["set_property", "sub-visibility", False]
        sent.clear()
        screensaver.set_brightness(40)
        assert ["set_property", "sub-visibility", True] in sent
        sent.clear()
        screensaver.set_brightness(60)     # no visibility change, no IPC
        assert sent == [["vf", "set", screensaver._vf(60)]]
        screensaver.set_subtitles(False)
        assert sent[-1] == ["set_property", "sub-visibility", False]
    finally:
        screensaver.ipc = saved


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as ex:
                fails += 1
                print(f"  FAIL {name}: {ex}")
    print("SUBTITLE-TESTS", "PASS" if not fails else f"FAIL ({fails})")
    sys.exit(1 if fails else 0)
