# Agent onboarding (30 seconds)

You are pointed at the **arcade server**. One CLI drives everything:

```
arcadectl --json <command>        # every command supports --json
```

(In the sim it lives inside the server container:
`cd sim && docker compose exec -T server arcadectl ...`.)

## What this system is

A retro arcade cabinet (`cabd` daemon) controlled by this server (`arcaded`).
All configuration lives in the server's **state repo** (git; every change is
a commit) and is pushed to the cabinet — never hand-edit rendered configs on
the cabinet; they are stomped from state before every launch.

## The commands you'll actually use

```
arcadectl status                          # online? what's running?
arcadectl games launch --system snes --path /roms/snes/x.sfc | kill | running
arcadectl volume get | set --master 70 | set --class trailers --mute
arcadectl screensaver status|on|off | set --brightness 60 --idle 300
arcadectl screensaver set --subtitles on|off # English subs, best track auto
arcadectl screensaver sources | enable-source <id> | disable-source <id>
arcadectl screensaver disable --for 2h
arcadectl screensaver remove-source <id>  # off the rotation board entirely
arcadectl controls map --scope system:snes --set a=b | save-preset <n> | load-preset <n>
arcadectl controls bindings --scope global | unbind --scope global <btn>
arcadectl controls scopes | devices       # what can be bound, and on what
arcadectl controls monitor --for 5        # live button presses
arcadectl art status | warm | push        # box art (libretro-thumbnails)
arcadectl roms search <q> | request <q> | status
arcadectl state log | rollback <sha> | push | bundle
arcadectl favorites add|remove|list <system>/<rom>
arcadectl cab exec -- <shell>             # escape hatch (security is out of scope)
```

Exit codes: 0 ok · 1 remote command failed · 2 usage · 3 server unreachable ·
4 API error.

## Where things live

- `skills/<domain>/SKILL.md` — worked examples per domain (screensaver,
  audio, controllers, roms, games, diagnostics). Read the one you need.
- `state/` — the source of truth the server renders from (audio.yaml,
  screensaver.yaml, controllers/, library/).
- `assets/` — Y2K asset pack (skins, sprites, tagged GIF library). Served at
  `/assets`; the Pegasus theme wants a copy inside its own directory.
  Regenerate the GIF index with `python3 assets/build-gif-index.py`.
- `themes/pegasus-y2k/` — the cabinet home screen. `pegasus-redblue` is the
  plain fallback.
- Web UI (secondary to this CLI): `http://<server>:8000/ui`.
- `docs/UI-BACKEND-GAPS.md` — what the UI shows that the server cannot do
  yet, and what each one would take. Read before "fixing" an inert control.

No MCP. No prompts. Everything returns JSON with `--json`.
