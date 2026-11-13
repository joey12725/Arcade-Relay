# Hardware

The reference build is a two-player upright cabinet with a 4:3 panel and a
small x86 PC inside. Nothing here is exotic; the software assumes "a Linux box
with two USB encoders and a screen", and the specifics below are what it was
tuned against.

## Bill of materials

| Part | Reference | Notes |
| --- | --- | --- |
| Computer | x86-64 mini-PC, integrated GPU, Wi-Fi 6E | Runs 8/16-bit through PS1-era cores comfortably. Heavier games stream from a desktop instead. |
| Display | 4:3 panel, 1024x768 native | Set in `state/cabinet.yaml`. The screensaver pan-scans 16:9 video to fill it with no bars and no squish. |
| Controls | 2x Brook P4 encoder boards, one per player | 8-way stick + 8 buttons each. Any USB encoder works; see "Encoders" below. |
| Audio | Cabinet speakers on the PC's analog or HDMI output | PipeWire handles per-class volume. |
| Network | Wi-Fi to the home router | The cabinet only dials out. No port forwarding, no VPN required. |
| Optional | Gaming desktop on the same LAN | Sunshine or Apollo host for Moonlight streaming of PC games. |

The server can be anything that runs Docker or systemd: a NAS, a home server,
a small VPS. It does not need to share a network with the cabinet.

## Panel layout

Each player's panel has a stick and eight buttons in two rows. The default
layer is Nintendo-style (`cabinet/panel_layout.py`):

```
top row      [1 A]  [2 Y]  [3 L]      [4 R]
bottom row   [5 B]  [6 X]  [7 Start]  [8 Select]
stick        d-pad (digital hat)
```

Systems whose cores letter their buttons differently get their own base layer,
so a global remap written in SNES letters can't scramble them: Genesis puts
A/B/C on buttons 1-3, and arcade (MAME 2003-Plus) puts Button 1-6 in reading
order with Coin on Select. On top of that sit the global, per-system and
per-game remaps from the state repo.

The default chord is **hold Start + Select (buttons 7 + 8) for 2 seconds:
Home**, which kills whatever is running and returns to the menu. Chords are
evaluated per panel, so player 2 never triggers player 1's combo. Screensaver
and Lifeline hotkeys live under `hotkeys:` in `state/cabinet.yaml`, and any
chord can be rebound in the web remapper (capture a press, pick a hold time,
pick a target).

## Encoders

`cabinet/provision/99-arcade-controllers.rules` gives the encoders stable
permissions and hides the raw devices from games. Brook P4 boards enumerate
under two identities depending on the mode they power up in (PS4 mode
`0c12:0c30`, XInput mode `045e:02ea`); both are covered. For a different
encoder, add its `idVendor`/`idProduct` from `lsusb`.

`cabinet/provision/controllers/panels.json` maps each **physical USB port** to
a player and calibrates its axes:

```json
{"player": 1, "name": "Arcade Left Panel",  "usb_port": "8.4.1", "axes": {...}},
{"player": 2, "name": "Arcade Right Panel", "usb_port": "8.4.4", "axes": {...}}
```

`panels.py` reads the raw boards and emits two persistent virtual pads through
`/dev/uinput`. The virtual pads stay present when a board is unplugged, so
RetroArch never renumbers players mid-game, and a board that comes back in a
different USB mode is still the same player. Find your port paths with
`arcadectl controls devices` or `udevadm info /dev/input/eventN`.

## Overscan

CRT-style bezels hide the edge of the panel. The Pegasus theme draws inside a
centered 90% x 96% safe area; adjust it in `themes/pegasus-y2k/theme.qml` if
your bezel is tighter or looser.

## Bringing up a new cabinet

1. Install Debian stable, minimal (no desktop task), and join Wi-Fi.
2. Clone the repo and run the provisioner with your server's hostname:

   ```sh
   sudo SERVER_HOST=arcade.example.com ./cabinet/provision/install.sh
   ```

   It sets up auto-login into an Openbox kiosk, disables blanking (the
   screensaver owns idle), installs `cabd`, `pegasus` and `steam` as systemd
   user units with `Restart=always`, installs the udev rules, and enables the
   15-minute self-update timer.
3. Install Pegasus and copy the theme plus `assets/` into its themes folder
   ([deploy.md](deploy.md) section 3).
4. Walk [hardware-checklist.md](hardware-checklist.md): combo hold times,
   panel calibration, the 4:3 crop, real reconnects after a router reboot.

Remote hands for the chores that need a screen: `arcadectl cab screenshot`,
`arcadectl cab remote-view` (VNC through the server), and
`arcadectl cab exec -- <command>`.
