# Skill: games

**Purpose:** launch/kill anything on the cabinet — emulated games, Steam
streams (Moonlight), xCloud (Greenlight) — and know what's running.

## Commands

```
arcadectl status                          # online? running? screensaver?
arcadectl games running
arcadectl games launch --system snes --path /roms/snes/smw.sfc
arcadectl games launch --system stream --path "moonlight:Street Fighter 6"
arcadectl games launch --system stream --path "greenlight:Game Pass Cloud"
arcadectl games kill                      # back to the frontend
arcadectl favorites add snes/smw.sfc | remove | list
```

## Guarantees you can rely on

- Configs are re-rendered from the state repo before every launch (CTL-4).
- A crashed or hung game is auto-recovered to the frontend within 2s (the
  watchdog); streams are never hang-killed (no command interface).
- Launches are local-first: the cabinet launches with the network cut; the
  server's view converges on reconnect.

## Worked example — "what is the cabinet doing right now?"

```
$ arcadectl --json status
# .cabinet_state.running  -> "snes/smw.sfc" | "stream/moonlight:..." | null
# .cabinet_state.screensaver -> true/false
$ arcadectl cab exec -- xdotool getactivewindow getwindowname   # literal truth
```
