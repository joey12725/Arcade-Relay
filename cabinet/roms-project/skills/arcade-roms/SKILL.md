---
name: arcade-roms
description: Install, verify, retry, reinstall, or launch games on the arcade cabinet from the server's game library. Use when the user asks to put a game on the cabinet, manage an install job, or play an installed game. Run the configured arcadectl on the cabinet; the server handles staging and verified delivery.
---

# Cabinet-side game install workflow

The cabinet's installed library and ROMs project are at `/roms`. This skill
also works from other cabinet projects. The installed `arcadectl` reads the
server address from `/etc/arcade/cabinet.env`; noninteractive agent sessions
need no export. Use `/usr/local/bin/arcadectl` if PATH does not find it.

1. Identify the full game title and system (`nes`, `snes`, `genesis`, `gb`,
   `gba`, `psx`, or `arcade`). Prefer cabinet-friendly digital controls.
2. Run `arcadectl --json status`. Confirm the cabinet is online.
3. Request through the server pipeline:
   `arcadectl --json roms request "Star Patrol" --system nes`.
   Record the returned job ID. Don't interpret `queued` as success.
4. For ambiguity, first run `roms search "title" --system <system>`, then
   request the chosen result with `roms request --id <candidate-id> --system
   <system>`. Verify title, region, and edition; prefer a two-player arcade
   set for a two-stick cabinet.
5. Poll `arcadectl --json roms status` at sensible intervals, with a bounded
   wait. Find the recorded ID; report errors and their `detail` faithfully.
   Use `roms retry <job-id>` when appropriate.
6. After `ready`, confirm the local installed path and compare `sha256sum`
   with the job's expected hashes. Check every disc track listed in `files`.
7. Launch only if requested: `arcadectl --json games launch --system <system>
   --path "<job.path>"`. Wait for the next heartbeat (about five seconds),
   confirm `arcadectl --json games running`, and report the title, system,
   path, integrity result, and launch result.

The server retains verified staged copies; `roms retry` can reinstall from
them with `source_reused: true`. Source checksums and cabinet checksums are
separate protections. Keep existing games, art, saves, and queue entries.

Use the configured CLI, not hand-copied files or hand-built candidate caches.
Don't modify or restart other server services for a game request.
