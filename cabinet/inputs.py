#!/usr/bin/env python3
"""Input sources + global combo engine (spec §5.2 #3-4).

ComboEngine is the production hold-detection logic: combos are parsed from
state/cabinet.yaml hotkeys ("hold:start:2s", "hold:start+select:5s"), fire
once when every named button has been held for the duration, and latch
until any of them is released. It is shared by both sources:

  - EvdevSource (real hardware): reads /dev/input/event* via python3-evdev,
    translating KEY/BTN codes to logical names. The device layer itself can
    only be exercised on hardware (this environment's kernel has no uinput —
    A-17); the engine below it is fully unit-tested.
  - the sim FIFO source in cabd: lines "<button> down|up" drive the same
    engine, so combo timing is tested end-to-end in sim.
"""
import re
import time

DEFAULT_HOLD_S = 2.0

# evdev code -> logical button (extend on hardware once the panel's encoder
# is known — OQ-9). Includes the alias names python3-evdev reports for the
# same codes (0x130 is BTN_A *and* BTN_SOUTH *and* BTN_GAMEPAD).
EVDEV_BUTTON_MAP = {
    "BTN_SOUTH": "a", "BTN_A": "a", "BTN_GAMEPAD": "a",
    "BTN_EAST": "b", "BTN_B": "b",
    "BTN_NORTH": "x", "BTN_X": "x",
    "BTN_WEST": "y", "BTN_Y": "y",
    "BTN_START": "start", "BTN_SELECT": "select",
    "BTN_TL": "l", "BTN_TR": "r",
    "BTN_DPAD_UP": "up", "BTN_DPAD_DOWN": "down",
    "BTN_DPAD_LEFT": "left", "BTN_DPAD_RIGHT": "right",
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right",
}


def resolve_button(code_name):
    """evdev gives a single name OR an alias tuple for one code; the logical
    button is whichever alias the map knows (verified: relying on [0] drops
    a/b entirely because BTN_A sorts before BTN_SOUTH)."""
    names = code_name if isinstance(code_name, (list, tuple)) else [code_name]
    for n in names:
        btn = EVDEV_BUTTON_MAP.get(str(n))
        if btn:
            return btn
    return None


def parse_combo(spec):
    """'hold:start+select:5s' -> (frozenset buttons, hold seconds)."""
    m = re.fullmatch(r"hold:([a-z0-9+_]+):(\d+(?:\.\d+)?)s?", spec.strip())
    if not m:
        raise ValueError(f"bad combo spec: {spec!r}")
    return frozenset(m.group(1).split("+")), float(m.group(2))


class ComboEngine:
    """Feed key_event(); call poll() regularly; on_combo(name) fires once
    per hold (latched until any member button releases)."""

    def __init__(self, hotkeys, on_combo, now=time.monotonic):
        self.combos = {}   # name -> (buttons, hold_s)
        for key, spec in (hotkeys or {}).items():
            name = key.replace("_combo", "")
            try:
                self.combos[name] = parse_combo(spec)
            except ValueError:
                continue
        self.on_combo = on_combo
        self.now = now
        self.held = {}     # button -> t_down
        self.latched = set()

    def key_event(self, button, is_down, t=None):
        t = self.now() if t is None else t
        if is_down:
            self.held.setdefault(button, t)
        else:
            self.held.pop(button, None)
            # any release un-latches combos containing that button
            self.latched = {n for n in self.latched
                            if button not in self.combos[n][0]}
        self.poll(t)

    def poll(self, t=None):
        t = self.now() if t is None else t
        fired = []
        for name, (buttons, hold_s) in self.combos.items():
            if name in self.latched:
                continue
            if buttons <= set(self.held) and \
                    all(t - self.held[b] >= hold_s for b in buttons):
                self.latched.add(name)
                fired.append(name)
                self.on_combo(name)
        return fired


class EvdevSource:
    """Real-hardware input reader. Emits (button, is_down) via callback and
    raw activity notifications. Requires /dev/input/event* devices (panel
    encoder / gamepads) — enumeration validated on hardware (A-17)."""

    def __init__(self, on_key, on_activity):
        self.on_key = on_key
        self.on_activity = on_activity
        self.devices = []

    def open_devices(self):
        try:
            import evdev
        except ImportError:
            return []
        from evdev import ecodes
        self.devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    self.devices.append(dev)
            except OSError:
                continue
        return self.devices

    async def run(self):
        """asyncio reader over all key-capable devices."""
        import asyncio
        import evdev
        from evdev import ecodes
        if not self.open_devices():
            return False

        async def reader(dev):
            async for ev in dev.async_read_loop():
                if ev.type != ecodes.EV_KEY or ev.value == 2:   # ignore repeat
                    continue
                code = ecodes.keys.get(ev.code) or ecodes.BTN.get(ev.code)
                btn = resolve_button(code)
                self.on_activity()
                if btn:
                    self.on_key(btn, ev.value == 1)

        await asyncio.gather(*(reader(d) for d in self.devices))
        return True
