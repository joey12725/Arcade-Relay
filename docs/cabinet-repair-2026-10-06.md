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
