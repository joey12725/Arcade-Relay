
## Follow-up: numbered buttons, calibrated directions, and hold chords

The user's subsequent live check confirmed the direction problems. Calibration
now follows the recorded order: reverse both axes on the left panel and swap
X/Y on the right panel. The Y2K PathView also lacked left/right navigation;
explicit increment/decrement handlers now browse the games. Up/down select
systems. Both axes have been exercised through the live UI after this change.

Panel numbering and observed evdev codes:

| Row | Leftmost | Second | Third | Rightmost |
| --- | --- | --- | --- | --- |
| Top | 1: 304 (Y) | 2: 307 (X) | 3: 309 (R1) | 4: 308 (L1) |
| Bottom | 5: 305 (B) | 6: 306 (A) | 7: 311 (R2) | 8: 310 (L2) |

Global server bindings, also saved in state/controllers/global.yaml:

- Hold 4+8 for 2 seconds: tap Start on that player's pad.
- Hold 3+7 for 2 seconds: tap Select on that player's pad.
- Hold 1+5 for 2 seconds: Home (kill game and return to frontend).

Each panel has its own chord engine. Inputs from different players cannot
complete a chord. A chord fires once until a member is released. Start/Select
are 120ms presses; holding the source buttons longer does not repeat them.
The eight ordinary action buttons continue to pass through to the game.
Right-side button 4 was absent from the original recording; the right Start
chord cannot work until that switch/wiring responds.

cabinet/panels.py grabs the raw Brook devices and publishes two persistent
calibrated player pads. The USB ports in panels.json retain player identity,
and unplugging a panel releases all its controls without removing its virtual
pad or renumbering the other player. Raw pads are hidden from RetroArch by
udev; Pegasus also receives an SDL ignore hint (its older SDL still lists one
inert raw device, but the exclusive grab prevents duplicate input). Virtual
pads include neutral X/Y axes for SDL 2.0.20's device classifier.

Live verification: RetroArch recognized Arcade Left Panel in port 1 and
Arcade Right Panel in port 2. A temporary diagnostic libretro core verified
all four Start/Select cases in the emulator itself: player 1 Start/Select
changed only player 1, and player 2 Start/Select changed only player 2, with
releases following each. Chords were generated using the production Panel
class with a controlled clock and written to the live virtual devices; this
does not certify the suspect physical switch. Evidence:
/tmp/panel-input-probe.log. tests/test_panels.py additionally checks the
recorded direction order, all button numbers, hold threshold/latching,
cross-player isolation, early release, and disconnect neutralization.

During follow-up validation the unattended updater reset the checkout to the
remote branch and re-provisioned, undoing locally committed repairs. The
repair was recovered from commit ea9df28 and the follow-up changes restored.
The updater now preserves dirty tracked files and local commits, and uses a
fast-forward merge instead of a hard reset. Its timer is re-enabled after
verification; local repairs require reconciliation with upstream before it
will install a newer remote revision. The cabinet's deploy key is read-only.
