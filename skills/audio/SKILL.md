# Skill: audio

**Purpose:** per-class audio policy. Classes: `games` (audible), `trailers`
(muted), `screensaver` (muted), `system`. Enforced on the cabinet's PipeWire
within ~100ms of any node appearing — a misbehaving app that un-mutes itself
gets re-clamped.

**State file:** `state/audio.yaml`.

## Commands

```
arcadectl volume get
arcadectl volume set --master 70
arcadectl volume set --class games --volume 100
arcadectl volume set --class trailers --mute
arcadectl volume set --class games --unmute
```

## Worked example — "the trailers are blasting audio"

```
$ arcadectl --json volume get       # check classes.trailers.muted — should be true
$ arcadectl volume set --class trailers --mute
$ arcadectl state log               # the change is a git commit; confirm it landed
```

If a node still sounds wrong on the cabinet:
`arcadectl cab exec -- pactl -f json list sink-inputs` shows every live
audio node with its media.role and mute state.
