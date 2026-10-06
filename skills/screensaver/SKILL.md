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
arcadectl screensaver enable-source <source-id>
arcadectl screensaver disable-source <source-id>
arcadectl screensaver on | off                  # force (SCR-6)
arcadectl screensaver disable --for 2h          # party mode: suppress idle start
arcadectl screensaver playlist                  # resolved stream list (debug)
arcadectl screensaver playlists list            # named playlists + which is active
arcadectl screensaver playlists create halloween --source <id> --source <id>
arcadectl screensaver playlists add halloween <id>...   # also: remove, delete
arcadectl screensaver playlists select halloween        # switch what plays
arcadectl screensaver playlists select all              # back to default rotation
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
effect on the next playlist fetch — **for the default rotation only**: if
`arcadectl --json screensaver playlists list` shows an active playlist that
contains the id, also run `arcadectl screensaver playlists remove <name> <id>`
(or `select all`), because an active playlist ignores the board. If the cabinet is offline it converges on
reconnect (state is pushed on connect).

## Playlists (anime / christmas / halloween / sfw / nsfw ...)

A playlist is a named set of sources. `select <name>` makes the screensaver
play exactly that set; `select all` returns to the default rotation (every
source switched ON in the rotation board). Switching takes effect
immediately if the screensaver is already running.

```
$ arcadectl --json screensaver sources                 # find ids (Plex rating keys)
$ arcadectl screensaver playlists create christmas --source 4821 --source 977
$ arcadectl screensaver playlists select christmas     # Christmas movies only
$ arcadectl screensaver playlists select all           # back to anime
```

- Playlist members play whether or not they are ON in the rotation board, so
  a seasonal or NSFW title need not be enabled (and leak into the default
  shuffle) to live in a playlist. **To keep something NSFW off the screen,
  leave it OFF the board and only put it in an NSFW playlist.** Likewise a
  family-safe playlist is just one that does not contain it.
- Names are case-insensitive. `all`, `all shows` and `default` mean the
  default rotation; those and `none` / `unknown` / `unavailable` can't be
  used as names. `create` on an existing name fails (exit 4, HTTP 409) —
  use `add` / `remove` to change a playlist.
- **While a playlist is active, the rotation board (enable/disable-source)
  does not change what plays** — it only shapes the default rotation. Check
  `playlists list` first; use `playlists remove` or `select all`.
- Selecting an empty playlist stops the screensaver (unless `fallback:
  dim_clock`) — the response carries a `warning`. Ids that Plex no longer
  has are skipped at play time; `create`/`add` warn about them.
- `playlists select` is also what Home Assistant calls: the cabinet device
  has a **Screensaver playlist** select entity whose options are
  `All shows` plus every playlist.
- Stored in `state/screensaver.yaml` as `playlists: {name: {sources: [...]}}`
  and `active_playlist: name|null`. Every change is a commit; ids are
  strings (quote them when hand-editing, though bare numbers are coerced).
- Switching playlists, or editing the active playlist's members, clears the
  cabinet's offline clip cache (an in-flight download is discarded too), so
  a link outage doesn't fall back to clips from the previous playlist.

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

