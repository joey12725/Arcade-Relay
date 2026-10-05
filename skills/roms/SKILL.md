---
name: roms
description: Request, download, verify, retry, or launch games on the arcade cabinet using arcadectl and the server's verified server-side ROM pipeline.
---

# Skill: roms

**Purpose:** get games onto the cabinet with one command. Flow:
request -> ROMDL downloads on the server -> checksum-verified delivery to the
cabinet over the tunnel -> art alongside -> frontend library rescan.

## Commands

```
arcadectl --json roms search "Gunstar Heroes" --system genesis
arcadectl --json roms request "Gunstar Heroes" --system genesis
arcadectl --json roms status            # queued|searching|downloading|delivering|ready|error
```

## From the cabinet itself

The cabinet's T3 Code **ROMs** project is `/roms`. Its `AGENTS.md`,
`ROM-DOWNLOADS.md`, and `skills/arcade-roms/SKILL.md` describe the workflow.
`/usr/local/bin/arcadectl` automatically reads `CABD_MEDIA_BASE` from
`/etc/arcade/cabinet.env`, so cabinet-side tasks need no laptop/server SSH,
environment export, or torrent client. The server handles downloading and
retention; finished files arrive on the cabinet automatically.

## Worked example — "get Gunstar Heroes on the cabinet"

```
$ arcadectl --json roms request "Gunstar Heroes" --system genesis
$ arcadectl --json roms status         # wait for this job's status = ready
$ arcadectl --json games launch --system genesis --path "/roms/genesis/Gunstar Heroes (USA).md"
```

Use full titles and an explicit supported system (`nes`, `snes`, `genesis`,
`gb`, `gba`, `psx`, `arcade`). For ambiguity, search then request the exact
candidate with `roms request --id <candidate-id> --system <system>`.
Inspect `detail` on errors and use `roms retry <job-id>`. Explicit retry can
reinstall from a retained verified archive; existing identical cabinet ROMs
are not rewritten. Verify local hashes against the completed job, and launch
only when the user asks to play. Preserve existing games, saves, and jobs.
