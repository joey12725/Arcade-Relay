# Skill: roms

**Purpose:** get games onto the cabinet with one command. Flow:
request -> ROMDL downloads on the server -> checksum-verified delivery to the
cabinet over the tunnel -> art alongside -> frontend library rescan.

## Commands

```
arcadectl roms search "metal slug"     # candidates from ROMDL
arcadectl roms request "metal slug 3"  # queue it; returns the queue entry
arcadectl roms status                  # queued|downloading|delivering|ready|error
```

## Worked example — "get Metal Slug 3 on the cabinet"

```
$ arcadectl roms request "metal slug 3"
$ arcadectl roms status                # poll until "ready"; detail = cabinet path
$ arcadectl games launch --system arcade --path /roms/arcade/Metal_Slug_3.bin
```

An `error` entry (e.g. link dropped mid-delivery) is re-queued by simply
requesting the same title again — delivery is checksum-verified, so a
half-transferred file can never be installed.
