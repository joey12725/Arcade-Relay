#!/usr/bin/env python3
"""Which subtitle track the screensaver carries, and how to mux it.

The owner gets one switch (screensaver.yaml `subtitles`), not a track list,
so the choice is made here: the best ENGLISH track per item, or none. It
works on plain dicts of the attributes Plex reports per <Stream>, so the
policy is testable without a Plex server (same split as shuffle.py).

"Best", in order:
  1. what it covers — full dialogue > SDH/CC/dubtitles > signs & songs /
     forced. The screensaver is muted by default (SCR-7), so the subtitles
     ARE the dialogue; a signs-only track is a last resort (some dub-only
     releases carry nothing else), commentary never.
  2. text over bitmap — mpv draws text at screen resolution and wraps it
     inside the visible area when the aspect mode crops the frame; a bitmap
     is fixed to the picture and loses whatever the crop cuts.
  3. a name that SAYS full/dialogue/subtitles over one that says nothing:
     the default flag is often on a signs track with an unhelpful name.
  4. plain over honorifics.
  5. ASS/SSA over plain text — anime releases carry the full translation,
     signs included, in the styled track.
  6. the file's own default flag, then embedded over a sidecar, then the
     first in the file.

Plex often holds several versions of one episode; the first plays, unless
it has no full-dialogue text track and another version does (choose()).

Releases label tracks loosely — mis-tagged languages, forced flags on full
tracks, default flags on signs tracks about as often as on dialogue — so
the track NAME is the main evidence, and flags only break ties. The regexes
below are the vocabulary seen across the enabled library (1,671 episodes).
"""
import codecs
import re

# Plex <Stream streamType>
VIDEO, AUDIO, SUBTITLE = "1", "2", "3"

# Plex `codec` values -> how the track gets into the matroska stream
COPY_TEXT = {"ass", "ssa", "srt", "subrip"}
CONVERT_TEXT = {"mov_text", "tx3g", "webvtt", "vtt", "smi", "sami", "text"}
BITMAP = {"pgs", "hdmv_pgs_subtitle", "vobsub", "dvd_subtitle",
          "dvb_subtitle", "dvbsub"}
STYLED = {"ass", "ssa"}

# Sidecar codecs we take, and the extension the fetched copy is saved under
# (ffmpeg detects the format from content; a forced -f that is wrong yields
# a silently EMPTY track, so none is passed). Text only: a .sup sidecar
# loses its timestamps on the way in, and an .idx is useless without its
# .sub.
SIDECAR = {"srt": "srt", "subrip": "srt", "ass": "ass", "ssa": "ssa",
           "vtt": "vtt", "webvtt": "vtt", "smi": "smi", "sami": "smi"}

# e.g. "Full Subtitles [DDY/SCY]", "Dialog - ENG (ASS)", "Dialouge [ENG]",
# "ASS / Main" | "Signs/Songs (CR/Commie)", "English (Forced)", "S&S",
# "Tittles" | "English (SDH)", "CC", "English (Dubtitle)", "[ASS] [FOR DUB]"
_FULL = re.compile(r"\bfull\b|\bdialo?u?gu?e?s?\b|\bmain\b", re.I)
_NAMED_FULL = re.compile(_FULL.pattern + r"|\bsubtitles?\b|\bsubs\b", re.I)
_SIGNS = re.compile(r"\bsigns?\b|\bsongs?\b|\bforced\b|\bkaraoke\b|"
                    r"\blyrics\b|\bop\s*/\s*ed\b|\btit+les?\b|\bs\s*&\s*s\b",
                    re.I)
_ALT = re.compile(r"\bsdh\b|\bcc\b|closed.?caption|hearing|\bdubtitles?\b|"
                  r"\bfor dubs?\b", re.I)
_HONORIFICS = re.compile(r"(?<!no )honorifics?", re.I)
_COMMENTARY = re.compile(r"commentary", re.I)
_ENGLISH = re.compile(r"\beng(lish)?\b", re.I)
_JAPANESE = re.compile(r"japanese|\bjpn?\b|日本", re.I)

# Plex's average kbps for a bitmap track: signs-only PGS runs 2-12, full
# dialogue 28-85 (155 pairs in the library)
SIGNS_MAX_KBPS = 15

# FULL: the translation. ALT: dialogue, but captions (SDH/CC) or the dub
# script (dubtitles). SIGNS: signs & songs / forced only.
FULL, ALT, SIGNS = 3, 2, 1


def _flag(s, k):
    return str(s.get(k) or "") in ("1", "true", "True")


def _codec(s):
    return (s.get("codec") or "").lower()


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def is_external(s):
    """A sidecar file next to the media: Plex serves it by key and it has no
    stream index inside the container."""
    return s.get("index") in (None, "") and bool(s.get("key"))


def is_english(s):
    code = (s.get("languageCode") or "").lower()
    tag = (s.get("languageTag") or "").lower()
    lang = (s.get("language") or "").lower()
    title = s.get("title") or ""
    # "enm" is the code releases borrow for an English-with-honorifics track
    if code in ("eng", "en", "enm") or tag in ("en", "eng", "enm") \
            or tag.startswith("en-") or lang.startswith("english"):
        return True
    # Mis-tagged English is common, almost always as the audio's language:
    # "English" tagged jpn (Initial D, My Hero Academia S1), and a full
    # translation named "Subtitles [FFF]" tagged jpn (Kill la Kill). Real
    # Japanese tracks are named "SDH", "Dubtitle", "Japanese (SDH)" or
    # nothing, so a jpn track only counts when its name says so.
    if _ENGLISH.search(title):
        return True
    return (code in ("jpn", "ja") or tag == "ja") \
        and bool(_NAMED_FULL.search(title)) and not _JAPANESE.search(title)


def coverage(s, trust_forced=True):
    """FULL / ALT / SIGNS, or 0 for a track that is never wanted."""
    title = s.get("title") or ""
    if _COMMENTARY.search(title):
        return 0
    if _FULL.search(title):
        return FULL           # "Full Subtitles [Signs & Songs]" is full
    if (trust_forced and _flag(s, "forced")) or _SIGNS.search(title):
        return SIGNS
    if _flag(s, "hearingImpaired") or _ALT.search(title):
        return ALT
    if _codec(s) in BITMAP and 0 < _num(s.get("bitrate")) <= SIGNS_MAX_KBPS:
        return SIGNS          # unnamed, and too light to be dialogue
    return FULL


def honorifics(s):
    return (s.get("languageCode") or "").lower() == "enm" \
        or bool(_HONORIFICS.search(s.get("title") or ""))


def muxable(s):
    c = _codec(s)
    if s.get("embeddedInVideo") or c.startswith("eia_") or c == "cea_608":
        return False          # captions inside the video bitstream
    if is_external(s):
        return c in SIDECAR
    if s.get("index") in (None, ""):
        return False
    return c in COPY_TEXT or c in CONVERT_TEXT or c in BITMAP


def _rank(s, trust_forced=True):
    c = _codec(s)
    return (coverage(s, trust_forced),
            0 if c in BITMAP else 1,
            1 if _NAMED_FULL.search(s.get("title") or "") else 0,
            0 if honorifics(s) else 1,
            1 if c in STYLED else 0,
            1 if _flag(s, "default") else 0,
            0 if is_external(s) else 1,
            -_num(s.get("index"), 1 << 20))


def _english(streams):
    return [s for s in streams
            if s.get("streamType") == SUBTITLE and is_english(s)
            and muxable(s) and coverage(s)]


def _best(streams):
    """(track, rank) of the best English track in one file, or (None, None).

    The forced flag only counts as evidence next to an unforced English
    track: some releases (SENPAI, KAWAII) flag their one English track —
    the full translation — forced."""
    subs = _english(streams)
    if not subs:
        return None, None
    trust = not all(_flag(s, "forced") for s in subs)
    best = max(subs, key=lambda s: _rank(s, trust))
    return best, _rank(best, trust)


def pick(streams):
    """The subtitle stream to carry, or None."""
    return _best(streams)[0]


# the languages anime and donghua are made in: a release with none of them
# in its audio is a dub release
ORIGINAL_AUDIO = {"jpn", "ja", "zho", "chi", "zh", "kor", "ko"}


def _unproven_full(streams, sub):
    """A FULL rating that rests on nothing: the name says nothing ("English
    (US)"), the file has no signs or captions track it would be the full
    counterpart of, and it is a dub release (no original-language audio).
    There that track is the signs track about as often as not (My Hero
    Academia, Re:ZERO, Buddy Daddies) — no reason to leave the first
    version for it."""
    if _NAMED_FULL.search(sub.get("title") or ""):
        return False
    if any(0 < coverage(s) < FULL for s in _english(streams) if s is not sub):
        return False
    audio = [s for s in streams if s.get("streamType") == AUDIO]
    return bool(audio) and not any(
        (a.get("languageCode") or "").lower() in ORIGINAL_AUDIO for a in audio)


def choose(versions):
    """(version, subtitle) for an item Plex holds as one or more versions
    ([{"streams": [...], ...}], Plex's order).

    The first version plays — it is what played before subtitles existed
    — unless another one carries a better track: what it covers, then text
    over bitmap (e.g. a BD remux with two unnamed PGS tracks next to a
    version with a proper ASS one). Names and flags alone are not worth a
    switch, and an alternative has to prove its track is full dialogue.
    Equal tiers keep the earlier version."""
    tiers, picks = [], []
    for i, v in enumerate(versions):
        sub, rank = _best(v["streams"])
        if sub is None:
            tier = (0, 0)
        else:
            tier = rank[:2]
            if i and tier[0] == FULL and _unproven_full(v["streams"], sub):
                tier = (SIGNS, tier[1])
        tiers.append(tier)
        picks.append((v, sub))
    best = max(range(len(picks)), key=lambda i: tiers[i])
    return picks[best] if tiers[best] > tiers[0] else picks[0]


def pick_audio(streams, language=None):
    """The audio stream ffmpeg would have chosen on its own.

    Mapping a subtitle means mapping every stream explicitly, which turns
    ffmpeg's automatic choice off; this replays it so turning subtitles on
    never changes which audio plays: default disposition first, then most
    channels, first in the file on a tie (ffmpeg 7 map_auto_audio).

    `language`: keep to that language when the file has it — for a version
    choose() switched to, whose own default may be another dub entirely
    (Demon Slayer's NF release defaults to Brazilian Portuguese)."""
    cands = [s for s in streams
             if s.get("streamType") == AUDIO and s.get("index") not in (None, "")]
    if language:
        cands = [s for s in cands if (s.get("languageCode") or "").lower()
                 == language.lower()] or cands
    best, best_score = None, -1
    for s in cands:
        score = _num(s.get("channels")) \
            + (5_000_000 if _flag(s, "default") else 0)
        if score > best_score:
            best, best_score = s, score
    return best


def to_utf8(data):
    """A sidecar's bytes as UTF-8.

    Sidecars come in whatever the ripper saved. ffmpeg mangles UTF-16 (a
    non-ASCII character opening a line loses its trailing bytes) and, when
    it has to convert, aborts the whole stream ~20 s in on cp1252 — so the
    server decodes it: BOM first, then strict UTF-8, then cp1252, which
    maps nearly every byte and is what non-UTF-8 English subtitles are."""
    for bom, enc in ((codecs.BOM_UTF8, "utf-8-sig"),
                     (codecs.BOM_UTF16_LE, "utf-16"),
                     (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(enc, errors="replace").encode("utf-8")
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace").encode("utf-8")


def codec_args(s):
    """-c:s for the chosen track: copy what matroska can hold, convert the
    rest of the text formats to SubRip."""
    c = _codec(s)
    if c in CONVERT_TEXT:
        return ["-c:s", "subrip"]
    return ["-c:s", "copy"]
