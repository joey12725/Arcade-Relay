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
- [ ] input.monitor + global hotkey combos on real evdev devices (sim uses a FIFO
  event source per A-13); includes HOME/SCREENSAVER/LIFELINE combo grabs.
- [ ] M5 real-scale: Sunshine/Apollo on the desktop, real Moonlight pairing, SF6
  playable at 60fps over Wi-Fi 6E; Greenlight real sign-in + xCloud title
  (best-effort — document failure mode if Microsoft breaks it).
- [ ] Real Steam window tracking: the `steam-fake <appid>` window title only exists
  in the sim fake. Real Steam titles its game windows arbitrarily, so the current
  window-tracking path would time out (20s) and kill Steam. On hardware, either
  discover the window via the newly-mapped-window heuristic or run Steam sessions
  without window tracking (probe=False already exempts them from hang detection).
- [ ] Theme favorite-toggle divergence: the redblue theme's favorite button flips
  Pegasus's internal flag (star badge) only; state/library/favorites.yaml (via
  `arcadectl favorites ...`) is the source of truth for the generated Favorites
  collection. Decide on-cabinet whether to hide the toggle or bridge it (e.g. a
  Pegasus-side script watching its favorites DB and calling the server API).
- [ ] Fresh-cabinet boot view: with zero favorites Pegasus drops the empty Favorites
  collection, so the theme boots into the first system collection instead. Expected
  degradation — confirm it reads acceptably on the real screen.
- [ ] First boot of provisioned kiosk: auto-login -> startx -> openbox ->
  user units up (cabd/pegasus/steam), xset s off -dpms in effect, cabd
  connects through the real tunnel (cabinet/provision/install.sh ran only
  under static validation per A-19).
- [ ] udev rules: replace the placeholder DragonRise encoder ids in
  99-arcade-controllers.rules with lsusb values from the actual panel.
- [ ] Lifeline recovery commands on real hardware: cabd.service ships no
  LIFELINE_* overrides, so defaults apply — `systemctl start ssh`,
  `systemctl restart NetworkManager`, and no fallback transport. Verify ssh
  actually starts, confirm the NetworkManager unit name matches your install
  (some Debian setups use systemd-networkd/ifupdown), and set
  LIFELINE_TRANSPORT_CMD if you add Tailscale.
- [ ] Panel button for lifeline network restart: the "a" button triggers it.
  Confirm the physical button mapped to `a` is reachable/obvious on the
  lifeline screen, and that it does not fight any Pegasus binding.
- [ ] Dev toolchain on the real cabinet: run `cabinet/provision/devtools.sh`
  (install.sh calls it automatically). Verified end-to-end in a Debian
  container — Node 24, npm, Claude Code, Codex CLI, Tailscale, git, ripgrep,
  jq, tmux. On hardware, follow up with the one interactive step:
  `tailscale up` (browser auth, do it via remote view), then set
  LIFELINE_TRANSPORT_CMD="tailscale up" in cabd.service so lifeline can
  bring the fallback transport up by itself.
- [ ] T3 Code interactive bring-up (docs/t3-setup.md is the owner's guide;
  devtools.sh does everything non-interactive). On the cabinet, as the
  arcade user, in this order:
    1. `sudo tailscale up` (browser auth on any device on the tailnet)
    2. `sudo tailscale set --operator=arcade` if T3 reports it lacks
       permission to configure Tailscale Serve
    3. `claude auth login` and `codex login --device-auth` — MUST be the
       same Linux user that runs T3, or the provider picker comes up empty
    4. `npx --yes t3@latest serve --tailscale-serve` once interactively to
       get the pairing URL/QR, pair the Mac/Windows/phone, then
       `systemctl --user start t3-code` for the persistent service
    5. `npx --yes t3@latest project` from the cabinet to add repos under
       ~/dev (remote project creation in the GUI is limited)
  Verify both providers appear in the T3 picker; if one is missing it is
  almost always a PATH/user mismatch (docs/t3-setup.md §10 diagnostic).
- [ ] Confirm sleep really is masked on the real box (`systemctl status
  sleep.target`) — devtools masks it, but a desktop environment can
  re-enable inhibitors.
