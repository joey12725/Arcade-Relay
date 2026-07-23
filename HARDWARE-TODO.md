# HARDWARE-TODO

Things that can only be verified on real hardware. Not faked green in the sim.

- [ ] Real cabinet: Debian install, auto-login `arcade`, X11+Openbox kiosk boot.
- [ ] Real panel input (evdev devices for joystick/buttons); combo hold-times feel right.
- [ ] Real RetroArch cores per system: performance on the actual mini-PC (OQ-2).
- [ ] Physical 4:3 panel: confirm native resolution (OQ-1) and zero-bar panscan crop.
- [ ] Real Cloudflare Tunnel + real router-reboot reconnect test.
- [ ] Moonlight ↔ Sunshine/Apollo on the real desktop over Wi-Fi 6E: SF6 @ 60fps.
- [ ] Greenlight xCloud sign-in + playable title (best-effort tier).
- [ ] Steam one-time interactive login via remote view.
- [ ] Audio out on real cabinet speakers; master volume mapping sanity.
- [ ] 24h wall-clock heartbeat ≥99% on real network (accept-m0 ran accelerated in sim).
- [ ] accept-m0 at real scale: 24h wall-clock, 5s beats, real Cloudflare tunnel, real
  router reboot (A-9 runs it accelerated in sim).
- [ ] UI-4 navigation audit on the real Pegasus + redblue QML theme: every reachable
  screen operable with joystick + 2 buttons (spec §8 M1 accept; sim uses a fake
  frontend window, so this is real-hardware/real-Pegasus scope).
- [ ] pegasus-redblue QML theme itself (favorites-default boot collection, oversized
  tiles, muted trailers) — buildable off-cabinet but only meaningfully testable
  against real Pegasus.
- [ ] EMU-1 cycles at real play duration (spec: launch->play-5s->exit; sim runs 0.3s
  per A-10) on real RetroArch cores.
- [ ] Master volume persistence across sink suspend/resume on the real ALSA device
  (sim null sink needed an anchor stream — A-12).
