# Skill: controllers

**Purpose:** durable controller mappings. Three layers (global, per-system,
per-game); rendered from the state repo before EVERY launch, so nothing the
emulator does at runtime can scramble them (the next launch is clean).

**State files:** `state/controllers/global.yaml`,
`state/controllers/systems/<sys>.yaml`, `state/controllers/presets/<name>.yaml`.

## Commands

```
arcadectl controls map --scope global --set a=b --set x=y
arcadectl controls map --scope system:snes --set a=b
arcadectl controls save-preset <name>       # snapshot ALL layers
arcadectl controls load-preset <name>       # restore + push, next launch renders it
arcadectl controls list-presets
arcadectl controls monitor --for 5          # stream live button presses (test a panel)
arcadectl state log                         # who changed what, when
arcadectl state rollback <sha>              # undo to any point
```

## Worked example — "my SNES buttons are scrambled"

```
$ arcadectl state log                        # find the bad change
$ arcadectl state rollback <sha-before-it>   # or: load a known-good preset:
$ arcadectl controls load-preset default
# next launch renders clean configs; verify live:
$ arcadectl controls monitor --for 5         # press buttons, watch events
```
