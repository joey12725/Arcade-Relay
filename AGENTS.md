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
arcadectl screensaver sources | enable-source <id> | disable-source <id>
arcadectl screensaver disable --for 2h
arcadectl controls map --scope system:snes --set a=b | save-preset <n> | load-preset <n>
arcadectl controls monitor --for 5        # live button presses
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
- Web UI (secondary to this CLI): `http://<server>:8000/ui`.

No MCP. No prompts. Everything returns JSON with `--json`.
