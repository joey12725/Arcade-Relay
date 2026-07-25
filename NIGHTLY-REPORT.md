# NIGHTLY REPORT — 2026-07-25 (final)

**Status: DONE (v3).** The whole spec is implemented and tested, not just the
parts the sim made convenient. See `DONE.md` (v3 section) for the full record.

## What changed since the last "done"

DONE v2 had parked five spec surfaces in ASSUMPTIONS A-20 as "v1 deferrals".
They were software, not hardware, so P8 built them: per-game mapping scope,
synthetic input (`input.press`/`input.text`), lifeline recovery paths, the
CTL-3 interactive remapper, the web UI library page — plus source thumbnails
and the `mode`/`fallback` settings, which had been accepted by the API and
git-committed while doing nothing at all.

## Verification

- P9 full serial regression: **zero failures** (22/22 test targets,
  accept-m0…m5 3/3, accept-m6 3/3 with cold agents).
- Soak: **4h23m clean**, one unbroken window, all three chaos modes firing
  (458 partitions, 178 heartbeat-kills, 274 cabd-kills).
- ~9 adversarial reviews total; 4 FAIL verdicts, each catching real defects.
  New assertions are mutation-tested — removing a guard makes them fail.

## Notable

Two bugs were found by **accept-m6 cold agents** doing an unrelated task, and
five more by the final sweep — two of those inside code my own fixes had just
touched. A 5/6-flaky test turned out to be hiding four screen-ownership
races. And the sweep caught a 13-second local game launch stall behind a
stalled media stream before it shipped — a prime-directive-1 violation that
heartbeat monitoring could never have surfaced.

## For the human

Hardware bring-up is all that remains: follow `DEPLOY.md`, then work
`HARDWARE-TODO.md`. Watch items: real-Steam window tracking, the theme on
real Pegasus (Qt 5.15), udev encoder ids, and the lifeline recovery commands
(defaults assume systemd + NetworkManager). `server/romdl.py` is still the
contract-faithful fake — swap in the real downloader when ready.

The one genuine deferral is CTL-5 Bluetooth, which the spec itself puts out
of scope for v1.

The autonomous loop has stopped.
