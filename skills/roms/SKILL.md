---
name: roms
description: Install, verify, retry, or launch games on the arcade cabinet from the server's game library using arcadectl.
---

# Skill: roms

**Purpose:** get a game from the server's library onto the cabinet with one
command. Flow: request -> the server finds the file under `ROM_SOURCE_DIR`
and stages a verified copy -> checksum-verified delivery to the cabinet over
the tunnel -> box art alongside -> frontend library rescan.

## Commands

```
arcadectl --json roms backend           # library or fake, and which systems it covers
arcadectl --json roms search "Star Patrol" --system nes
arcadectl --json roms request "Star Patrol" --system nes
arcadectl --json roms status            # queued|searching|downloading|delivering|ready|error
```

## From the cabinet itself

The cabinet's **ROMs** project is `/roms`. Its `AGENTS.md` and
`skills/arcade-roms/SKILL.md` describe the workflow. `/usr/local/bin/arcadectl`
reads `CABD_MEDIA_BASE` from `/etc/arcade/cabinet.env`, so cabinet-side tasks
need no laptop, server SSH, or environment export. Finished files arrive on
the cabinet automatically.

## Worked example: "put Star Patrol on the cabinet"

```
$ arcadectl --json roms request "Star Patrol" --system nes
$ arcadectl --json roms status         # wait for this job's status = ready
$ arcadectl --json games launch --system nes --path "/roms/nes/Star Patrol.nes"
```

Use full titles and an explicit supported system (`nes`, `snes`, `genesis`,
`gb`, `gba`, `psx`, `arcade`). A title match scoring 100 or more is queued
unattended; anything fuzzier needs a search and an explicit
`roms request --id <candidate-id> --system <system>`. Inspect `detail` on
errors and use `roms retry <job-id>`. Retry reinstalls from the retained
verified copy; identical cabinet ROMs are not rewritten. Launch only when the
user asks to play. Preserve existing games, saves, and jobs.
