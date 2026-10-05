---
name: arcade-roms
description: Request, download, verify, retry, reinstall, or launch games from the arcade cabinet's ROMs project. Use when the user asks to get a game onto the cabinet, download a ROM, manage a download job, or play an installed game. Run the configured arcadectl on the cabinet; the server handles server-side downloading and verified delivery.
---

# Cabinet-side ROM download workflow

The cabinet's installed library and ROMs project are at `/roms`. This skill
also works from other cabinet projects. Read `/roms/ROM-DOWNLOADS.md` for
details. The installed `arcadectl` automatically reads the server address
from `/etc/arcade/cabinet.env`; noninteractive agent sessions need no export.
Use `/usr/local/bin/arcadectl` if PATH does not find it.

1. Identify the full game title and system (`nes`, `snes`, `genesis`, `gb`,
   `gba`, `psx`, or `arcade`). Prefer cabinet-friendly digital controls.
2. Run `arcadectl --json status`. Confirm the cabinet is online.
3. Request through the real server pipeline:
   `arcadectl --json roms request "Gunstar Heroes" --system genesis`.
   Record the returned job ID. Don't interpret `queued` as success.
4. For ambiguity, first run `roms search "title" --system <system>`, then
   request the chosen result with `roms request --id <candidate-id> --system
   <system>`. Verify title, region, and edition; prefer a two-player arcade
   set for a two-stick cabinet.
5. Poll `arcadectl --json roms status` at sensible intervals, with a bounded
   wait. Find the recorded ID; report errors and their `detail` faithfully.
   Use `roms retry <job-id>` when appropriate. Report the ID/status if the
   game is still downloading when you stop waiting.
6. After `ready`, confirm the local installed path and compare `sha256sum`
   with the job's expected hashes. Check every PS1 file/track in `files`.
7. Launch only if requested: `arcadectl --json games launch --system <system>
   --path "<job.path>"`. Wait for the next heartbeat (about five seconds),
   confirm `arcadectl --json games running`, and report
   the title, system, path, integrity result, and launch result.

The server retains verified archives; `roms retry` can reinstall without a
new torrent, with `source_reused: true`. Source checksums and cabinet checksums
are separate protections. Keep existing games, art, saves, and queue entries.

Use the configured CLI, not ad-hoc source URLs, manually constructed candidate
caches, or a local torrent client. Downloads belong to the server's dedicated
dedicated `arcade-roms` download category. Don't modify or restart other
server services for a game request.
