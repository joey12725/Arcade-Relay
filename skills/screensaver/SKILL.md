# Skill: screensaver

**Purpose:** the idle attract mode — streams the media library at 480p,
cropped to fill the 4:3 panel, with a settable black-tint brightness.

**State file:** `state/screensaver.yaml` (idle_seconds, brightness 0-100,
mode, sources[], fallback). Every change is a git commit; the cabinet is
pushed live.

## Commands

```
arcadectl screensaver status                    # current config
arcadectl screensaver set --brightness 60 --idle 300 --mode shuffle_episodes
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
