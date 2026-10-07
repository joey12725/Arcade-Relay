# Skill: controllers

**Purpose:** durable controller mappings. Three layers (global, per-system,
per-game); rendered from the state repo before EVERY launch, so nothing the
emulator does at runtime can scramble them (the next launch is clean).

**State files:** `state/controllers/global.yaml`,
`state/controllers/systems/<sys>.yaml`, `state/controllers/presets/<name>.yaml`.

## Commands

```
arcadectl controls map --scope global --set player1.button3=l2
arcadectl controls map --scope system:snes --set button2=x --set button6=y
arcadectl controls save-preset <name>       # snapshot ALL layers
arcadectl controls load-preset <name>       # restore + push, next launch renders it
arcadectl controls list-presets
arcadectl controls monitor --for 5          # stream live button presses (test a panel)
arcadectl state log                         # who changed what, when
arcadectl state rollback <sha>              # undo to any point
```

## Panel button layouts

Each panel has eight buttons: top row `1 2 3 4`, bottom row `5 6 7 8`.
Before every launch cabd works out which RetroPad button each panel button is,
per player (`cabinet/panel_layout.py`), and renders both panels' RetroArch
autoconfig into `/tmp/rendered/autoconfig`. Layers, lowest to highest:

1. built-in SNES-style base, every system, both players
2. `--scope global` entries
3. built-in Genesis / arcade layout: their cores do not use SNES letters, so
   a global remap made in SNES letters must not scramble Genesis A/B/C
4. `--scope system:<sys>` entries
5. `--scope game:<sys>/<rom>` entries

Defaults, in console terms:

| System | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SNES, GBA, GB | A | Y | L | R | B | X | Start | Select |
| NES (Nestopia) | A | Turbo B | FDS side | FDS eject | B | Turbo A | Start | Select |
| PSX (PCSX-ReARMed) | Circle | Square | L1 | R1 | Cross | Triangle | Start | Select |
| Genesis (GX) | A | B | C | X | Y | Z | Start | Mode |
| Arcade (MAME 2003-Plus) | Btn 1 | Btn 2 | Btn 3 | Btn 4 | Btn 5 | Btn 6 | Start | Coin |

GB only has A/B and GBA adds L/R; the other buttons do nothing there.

Entry keys are physical: `player1.button<N>` / `player2.button<N>` (one
player; what the web remapper writes when you press a panel button) or
`button<N>` (both players). Values are RetroPad names: `a b x y l r l2 r2
start select` (the table translates them; the stick always stays the
d-pad). Assigning moves a button: `button3=l2` takes L2 off whichever button
had it, and panel 3's old target (L) is unbound unless another entry claims
it. Any other key (`a: b`, ...) is a legacy logical key: it renders only to the
sim's `.rmp` / keyboard binds and never touches the panels.

```
arcadectl controls map --scope global --set player1.button3=l2   # P1 only, L unbound
arcadectl controls map --scope system:snes --set button1=b --set button5=a   # swap A/B
arcadectl controls map --scope game:psx/<rom> --set button3=l2 --set button4=r2
arcadectl controls unbind --scope game:psx/<rom> button3 button4  # back to L1/R1
```

PSX L2/R2 do not fit on eight buttons with everything else, so a game that
needs them gets a per-game remap like the `game:psx` line above.

Start and Select are plain buttons 7 and 8, no chords. **Home** (quit the game)
is a hold chord in `global.yaml` `bindings:`, seeded as 7+8 held 2 s; see it
with `arcadectl controls bindings --scope global`, change it with
`controls bind` / `controls unbind-combo --buttons button7+button8 --hold 2s`.

## Worked example — "my SNES buttons are scrambled"

```
$ arcadectl state log                        # find the bad change
$ arcadectl state rollback <sha-before-it>   # or: load a known-good preset:
$ arcadectl controls load-preset default
# next launch renders clean configs; verify live:
$ arcadectl controls monitor --for 5         # press buttons, watch events
$ arcadectl cab exec -- cat /tmp/rendered/autoconfig/Arcade_Left_Panel.cfg  # what P1 got
```
