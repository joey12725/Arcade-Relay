# ROMs on the arcade cabinet

You are running **on the cabinet**, in its **ROMs** project (`/roms`). To
install a game from the server's library, read **skills/arcade-roms/SKILL.md**,
then use:

```sh
arcadectl --json status
arcadectl --json roms request "Star Patrol" --system nes
arcadectl --json roms status
```

`arcadectl` already knows the server URL from `/etc/arcade/cabinet.env`.
No laptop, server terminal, environment export, or interactive login is needed.
If your agent's PATH is unusual, use `/usr/local/bin/arcadectl` explicitly.

The **server** owns the game library. It stages a checksum-verified copy and
delivers it to this cabinet over the tunnel. Use the request API instead of
copying ROM files in by hand, so every install is verified and recorded.

Specify the system: `nes`, `snes`, `genesis`, `gb`, `gba`, `psx`, or `arcade`.
Use full titles; choose an exact candidate if a search is ambiguous. Preserve
existing ROMs, art, saves, and jobs. Report success only after the job is
`ready` and its installed path is present. Launch only when requested, since
launching replaces the game currently running on the cabinet.

For errors, inspect the job's `detail`, then use `roms retry <job-id>`.
Don't restart unrelated services on the server to fix a game request.
