# Cabinet repair, 2026-10-06

The running cabinet had been replaced with code lacking the previously committed
panel bridge, local HA publisher, and launch wait behavior. The checkout still
contained those fixes. Restored them while merging the deployed ROM-delivery
protections, asynchronous system.exec, companion-file metadata, and quoted ROM
paths into the source checkout.

Game visibility: restored --wait metadata, explicitly hide Pegasus while a game
owns the screen, activate/map it on return, and queue library-triggered reloads
until the game has ended and deliveries have been quiet for ten seconds. Pegasus
now has a three-second stop timeout instead of hanging for ninety seconds.

Input: restored persistent per-player calibrated pads. The previously missing
pegasus.env is now checked in, with explicit digital-hat mappings and no trigger,
analog-stick, or shoulder navigation mappings. Buttons 7/8 remain game buttons
and chord inputs. Unchanged state syncs no longer reset a hold in progress.
The existing calibration in panels.json is used: the reported faults were from
the uncalibrated raw devices exposed by the replaced runtime.

Display: added a centered 90% horizontal / 96% vertical transform to the menu,
leaving 5% per side and 2% top/bottom safe margins for the physical overscan.
A screenshot verifies the inset layout; physical screen fit needs user feedback.

Validation: controller, hardware, launch-wait, and new deferred-reload tests pass.
Launched an installed game through the panel into Pegasus; confirmed the --wait
child remains alive and RetroArch owns the active window. Screenshots show the
game unobstructed. Injected raw controller events verify calibrated directions,
button 7/8 isolation from axes, and Home returning to the menu. Physical switch
health is not certified by injected tests; right top-row button 4 remains the
previously suspect switch. No ROM files or active downloads were changed.

## Follow-up: frozen menu after screensaver wake (afternoon)

Symptom: the stick woke the cabinet from the screensaver, but neither the sticks
nor a keyboard moved the menu, while it kept animating.

Cause: cabd found the frontend with `xdotool search --name pegasus`, a
case-insensitive regex. It also matches Qt's two hidden helper windows
("pegasus-frontend" client leader, "Qt Selection Owner for pegasus-fe"), and
xdotool's breadth-first search returns them before the real window. Before this
morning's repair a bad match was harmless: focusing an unmapped window just
failed. The repair added `windowmap` + `windowactivate` to `focus_window`, so
each screensaver wake mapped a 1x1 helper over the menu and gave it keyboard
focus. Pegasus only forwards pad navigation to Qt's focused window, so both
inputs were dead. The same match made `hide_frontend` a silent no-op.

Fix: `FRONTEND_TITLE=^Pegasus$` in cabd.service (anchored, like the stream
titles). `hide_frontend` is removed rather than finally made effective:
unmapping Pegasus makes Openbox drop its fullscreen state (it came back
windowed with a title bar), and a cabd restart mid-game would have left the
menu unmapped. Games started from the menu are unaffected either way, because
Pegasus tears down its own window around launches it starts.

Validation: tests/test_frontend_window.py. On the cabinet, with Qt focus and
key logging temporarily added to the deployed theme: screensaver wakes, remote
game launch/kill, and Pegasus restarts (including during the screensaver) all
end with the menu fullscreen, active, and taking keys. The Qt helper windows
stay unmapped.

Testing note: a system change takes about 1-3 s to appear on screen, and the
first one after a Pegasus restart about 2.5-4 s. A screenshot check with a
shorter wait reports input as dead when it is not.
