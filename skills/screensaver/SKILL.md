# Skill: screensaver

**Purpose:** the idle attract mode — streams the media library at 480p,
cropped to fill the 4:3 panel, with a settable black-tint brightness.

**State file:** `state/screensaver.yaml` (idle_seconds, brightness 0-100,
mode, sources[], fallback, subtitles). Every change is a git commit; the
cabinet is pushed live.

## Commands

```
arcadectl screensaver status                    # current config
arcadectl screensaver set --brightness 60 --idle 300 --mode shuffle_episodes
arcadectl screensaver set --subtitles on        # English subtitles (on|off)
arcadectl screensaver sources                   # every series/movie + enabled flag
arcadectl screensaver enable-source <jellyfin-id>
arcadectl screensaver disable-source <jellyfin-id>
arcadectl screensaver on | off                  # force (SCR-6)
arcadectl screensaver disable --for 2h          # party mode: suppress idle start
arcadectl screensaver playlist                  # resolved stream list (debug)
```

## Worked example — "make the screensaver dimmer and disable Redline"

```
$ arcadectl --json screensaver status           # see current brightness (e.g. 80)
$ arcadectl screensaver set --brightness 40     # dimmer (0=black, 100=untinted)
$ arcadectl --json screensaver sources          # find the id whose name starts "Redline"
$ arcadectl screensaver disable-source <that-id>
$ arcadectl --json screensaver sources          # confirm enabled:false
```

Brightness applies live (<500ms) if the screensaver is running. Sources take
effect on the next playlist fetch. If the cabinet is offline it converges on
reconnect (state is pushed on connect).

## Subtitles

One switch: `arcadectl screensaver set --subtitles on|off` (web UI:
SCREENSAVER POLICY → Subtitles). English only, and there is no track
picker — the server chooses per episode (`server/subtitles.py`):

1. full dialogue over SDH/CC over signs-&-songs/forced (the screensaver is
   muted, so the subtitles are the dialogue); commentary never
2. text (ASS/SRT) over bitmap (PGS) — text is drawn at screen resolution
   and wrapped inside the visible area when `aspect: crop` cuts the frame
3. a name that says full/dialogue, plain over honorifics, ASS over SRT, the
   file's default flag, embedded over a sidecar

Track names beat flags (releases flag signs tracks default about as often
as dialogue), and mis-tagged English is recognised by name. With subtitles
on, when Plex holds several versions of an episode and another one has a
clearly better English track, that version plays instead — in the first
version's audio language when it has it. An episode with no usable English
track simply plays without.

The track is carried on every stream either way; the switch shows or hides
it (mpv `sub-visibility`), so it flips on the episode already playing and
the offline clip cache follows it too.

Limits: mpv draws subtitles after the brightness tint, so they stay at full
brightness (at `--brightness 0` they are hidden). Positioned signs near the
top/bottom of a 4:3 episode can still be lost to `aspect: crop`. Release
fonts are not carried (up to ~47 MB per episode); the cabinet's own fonts
are used.

Check what an episode got: `ffprobe <server>/media/<ratingKey>` lists at
most one subtitle stream, tagged `eng`.

