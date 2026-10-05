# ROMs on the arcade cabinet

You are running **on the cabinet**, in its T3 Code **ROMs** project (`/roms`).
To download/request/install a game, read **ROM-DOWNLOADS.md** and
**skills/arcade-roms/SKILL.md**, then use:

```sh
arcadectl --json status
arcadectl --json roms request "Gunstar Heroes" --system genesis
arcadectl --json roms status
```

`arcadectl` already knows the server URL from `/etc/arcade/cabinet.env`.
No laptop, server terminal, environment export, or interactive login is needed.
If your agent's PATH is unusual, use `/usr/local/bin/arcadectl` explicitly.

The **server** downloads via server-side download client and delivers verified
files to this cabinet. This project is the installed library, not a torrent
workspace. Use the request API instead of manually downloading/copying ROMs.

Specify the system: `nes`, `snes`, `genesis`, `gb`, `gba`, `psx`, or `arcade`.
Use full titles; choose an exact candidate if a search is ambiguous. Preserve
existing ROMs, art, saves, and jobs. Report success only after the job is
`ready` and its installed path is present. Launch only when requested, since
launching replaces the game currently running on the cabinet.

For errors, inspect the job's `detail`, then use `roms retry <job-id>`.
Don't restart unrelated services on the server to fix a
game request. This workflow needs no changes to other server services.
