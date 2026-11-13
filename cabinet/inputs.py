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
import os
import re
import time
from collections import namedtuple

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
    "BTN_TL2": "l2", "BTN_TR2": "r2",
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


# --------------------------------------------------------------- bindings
#
# User-defined combo / hold bindings (spec §5.7, extended). These CANNOT be
# pushed down into RetroArch: a mapping layer renders to retroarch.cfg and
# .rmp files, and RetroArch's remap format has no notion of "these two
# buttons, held for 2 seconds". So cabd owns them — it already reads evdev
# for the cabinet hotkeys, and this is the same detection generalised.
#
# A binding's target is one of two kinds:
#   action  — a cabinet action cabd performs itself (screensaver, save state,
#             quit...). Works everywhere, no extra plumbing.
#   button  — a logical pad button, emitted through a uinput virtual pad so
#             the emulator sees a real gamepad event. Needs /dev/uinput, which
#             a container does not have (A-17); VirtualPad reports that
#             instead of failing silently.

PAD_TARGETS = ("a", "b", "x", "y", "l", "r", "l2", "r2",
               "start", "select", "up", "down", "left", "right")
ACTION_TARGETS = ("home", "screensaver", "lifeline", "save_state",
                  "load_state", "pause", "reset", "quit",
                  "fast_forward", "rewind", "screenshot")


def target_kind(target):
    if target in ACTION_TARGETS:
        return "action"
    if target in PAD_TARGETS:
        return "button"
    raise ValueError(f"unknown binding target {target!r}")


def parse_binding(entry):
    """One YAML binding -> (frozenset buttons, hold_s, target).

    Raises ValueError on anything malformed; callers skip bad entries rather
    than letting one typo in the state repo take the whole engine down.
    """
    if not isinstance(entry, dict):
        raise ValueError(f"binding must be a mapping, got {type(entry).__name__}")
    buttons = entry.get("buttons")
    if isinstance(buttons, str):
        buttons = buttons.split("+")
    if not buttons or not isinstance(buttons, (list, tuple)):
        raise ValueError("binding needs a non-empty `buttons` list")
    buttons = frozenset(str(b).strip().lower() for b in buttons if str(b).strip())
    if not buttons:
        raise ValueError("binding needs at least one button")
    target = str(entry.get("target") or "").strip().lower()
    target_kind(target)             # validates
    hold_ms = entry.get("hold_ms", 0)
    try:
        hold_ms = int(hold_ms)
    except (TypeError, ValueError):
        raise ValueError(f"hold_ms must be an integer, got {hold_ms!r}") from None
    if hold_ms < 0:
        raise ValueError("hold_ms must not be negative")
    return buttons, hold_ms / 1000.0, target


def binding_expr(buttons, hold_s):
    """Human-readable form used by logs and the remapper table."""
    expr = "+".join(sorted(buttons))
    return f"{expr} (hold {hold_s:g}s)" if hold_s else expr


class BindingEngine:
    """Combo + hold detection for user bindings.

    Feed key_event(); call poll() regularly. on_fire(target, kind) runs once
    per hold, latched until one of the member buttons is released.

    Specificity: when a binding's button set is a strict subset of another
    binding that is *also* currently satisfied, only the larger one fires.
    Without that, binding l+r to START while L alone is bound to L means
    pressing both fires all three, which is never what anyone wants.
    """

    def __init__(self, entries, on_fire, now=time.monotonic):
        self.bindings = []      # (buttons, hold_s, target, kind)
        self.errors = []
        for entry in (entries or []):
            try:
                buttons, hold_s, target = parse_binding(entry)
            except ValueError as e:
                self.errors.append(str(e))
                continue
            self.bindings.append((buttons, hold_s, target, target_kind(target)))
        # longest first so specificity checks are cheap and firing order is
        # deterministic
        self.bindings.sort(key=lambda b: (-len(b[0]), sorted(b[0])))
        self.on_fire = on_fire
        self.now = now
        self.held = {}
        self.latched = set()

    def key_event(self, button, is_down, t=None):
        t = self.now() if t is None else t
        if is_down:
            self.held.setdefault(button, t)
        else:
            self.held.pop(button, None)
            self.latched = {k for k in self.latched if button not in k[0]}
        return self.poll(t)

    def poll(self, t=None):
        t = self.now() if t is None else t
        down = set(self.held)
        ready = []
        for buttons, hold_s, target, kind in self.bindings:
            if not buttons <= down:
                continue
            if hold_s and not all(t - self.held[b] >= hold_s for b in buttons):
                continue
            ready.append((buttons, hold_s, target, kind))

        fired = []
        for buttons, hold_s, target, kind in ready:
            # suppressed by a larger satisfied binding that contains us
            if any(buttons < other[0] for other in ready):
                continue
            key = (buttons, target)
            if key in self.latched:
                continue
            self.latched.add(key)
            fired.append((target, kind))
            self.on_fire(target, kind)
        return fired


class VirtualPad:
    """uinput gamepad so combo bindings can emit real pad buttons.

    The emulator reads the physical pad directly, so a synthesised button has
    to come from an actual input device — writing to RetroArch's network
    command port can trigger *actions* but cannot press B. Absent
    /dev/uinput (containers, and this repo's sim — A-17) `available()` is
    False and cabd reports that rather than pretending the binding worked.
    """

    UINPUT_CODES = {
        "a": "BTN_SOUTH", "b": "BTN_EAST", "x": "BTN_NORTH", "y": "BTN_WEST",
        "l": "BTN_TL", "r": "BTN_TR", "l2": "BTN_TL2", "r2": "BTN_TR2",
        "start": "BTN_START", "select": "BTN_SELECT",
        "up": "BTN_DPAD_UP", "down": "BTN_DPAD_DOWN",
        "left": "BTN_DPAD_LEFT", "right": "BTN_DPAD_RIGHT",
    }

    def __init__(self):
        self._dev = None
        self._why = "not opened"

    def available(self):
        return self._dev is not None

    def why(self):
        return self._why

    def open(self):
        try:
            import evdev  # noqa: F401  (availability probe)
            from evdev import UInput, ecodes
        except ImportError:
            self._why = "python3-evdev not installed"
            return False
        caps = {ecodes.EV_KEY: [getattr(ecodes, c)
                                for c in self.UINPUT_CODES.values()
                                if hasattr(ecodes, c)]}
        try:
            self._dev = UInput(caps, name="arcade-cabinet-virtual-pad")
            self._why = "ok"
            return True
        except (OSError, PermissionError) as e:
            self._dev = None
            self._why = f"/dev/uinput unavailable: {e}"
            return False

    def tap(self, button, hold_s=0.05):
        """Press and release a logical pad button."""
        if self._dev is None:
            return False
        from evdev import ecodes
        name = self.UINPUT_CODES.get(button)
        code = getattr(ecodes, name, None) if name else None
        if code is None:
            return False
        self._dev.write(ecodes.EV_KEY, code, 1)
        self._dev.syn()
        time.sleep(hold_s)
        self._dev.write(ecodes.EV_KEY, code, 0)
        self._dev.syn()
        return True

    def close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None


def list_devices():
    """Key-capable input devices, for the remapper's device picker.

    Returns [] rather than raising when evdev is missing (the sim uses the
    FIFO source and has no /dev/input at all) — the UI shows an empty picker
    instead of an error, which is the truth.
    """
    try:
        import evdev
    except ImportError:
        return []
    from evdev import ecodes
    out = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
            if ecodes.EV_KEY not in dev.capabilities():
                continue
            out.append({"path": path, "name": dev.name,
                        "phys": dev.phys or "", "id": f"{dev.info.vendor:04x}:"
                                                      f"{dev.info.product:04x}"})
        except OSError:
            continue
    return out


SYSFS_INPUT = "/sys/class/input"
EV_KEY_BIT = 1 << 1
NodeInfo = namedtuple("NodeInfo", "identity vendor product phys name ev")


def node_info(path, known=None):
    """Classify /dev/input/eventN from sysfs without opening it. On the
    cabinet's kernel each evdev close() waits out an RCU grace period
    (20-46 ms), so open/close-probing every node froze the input loop.
    None when there is nothing new: the node still holds device instance
    `known`, or sysfs is not complete yet (ask again next scan)."""
    node = os.path.join(SYSFS_INPUT, os.path.basename(path))

    def read(name):
        with open(os.path.join(node, "device", name)) as f:
            return f.read().strip()
    try:
        # The link names the inputNN instance, so it changes on every plug.
        # Read first: a replug racing the reads below then shows up as a
        # new identity next scan instead of being remembered wrongly.
        identity = os.readlink(node)
        if identity == known:
            return None
        return NodeInfo(identity, int(read("id/vendor"), 16), int(read("id/product"), 16),
                        read("phys"), read("name"), int(read("capabilities/ev").split()[-1], 16))
    except (OSError, ValueError, IndexError):
        return None


async def close_device(dev):
    """evdev close() blocks (see node_info), so do it in a thread. Unhook
    the fd from the loop first: python-evdev's close() only removes its
    reader when it can see the loop, which it cannot from a thread."""
    import asyncio
    if dev.fd >= 0:
        asyncio.get_running_loop().remove_reader(dev.fd)
    await asyncio.to_thread(dev.close)


class EvdevSource:
    """Real-hardware input reader. Emits (button, is_down) via callback and
    raw activity notifications. Requires /dev/input/event* devices (panel
    encoder / gamepads) — enumeration validated on hardware (A-17)."""

    def __init__(self, on_key, on_activity, ignore_device=None):
        self.on_key = on_key
        self.on_activity = on_activity
        self.ignore_device = ignore_device or (lambda dev: False)
        self.devices = []
        self.rejected = {}   # path -> identity of a device not worth opening

    def wanted(self, path):
        info = node_info(path, self.rejected.get(path))
        if info is None:
            return False
        if self.ignore_device(info) or not info.ev & EV_KEY_BIT:
            self.rejected[path] = info.identity
            return False
        return True

    def open_devices(self):
        """Paths run() will read; sysfs only, nothing is opened."""
        try:
            import evdev
        except ImportError:
            return []
        self.devices = [p for p in evdev.list_devices() if self.wanted(p)]
        return self.devices

    async def scan(self, tasks, reader):
        import asyncio
        import evdev
        from evdev import ecodes
        try:
            present = set(evdev.list_devices())
        except OSError:
            present = set()
        for path in [p for p in tasks if p not in present
                     or tasks[p].done()]:
            tasks.pop(path).cancel()
        for path in sorted(present - set(tasks)):
            if not self.wanted(path):
                continue
            try:
                dev = await asyncio.to_thread(evdev.InputDevice, path)
            except OSError:
                continue
            if self.ignore_device(dev) or ecodes.EV_KEY not in dev.capabilities():
                await close_device(dev)   # replaced since the sysfs read
                continue
            tasks[path] = asyncio.create_task(reader(dev))
        self.devices = list(tasks)

    async def run(self):
        """asyncio reader over all key-capable devices, with hot-plug.

        Devices are rescanned every few seconds: a controller that is
        unplugged mid-session (or plugged in after boot — replugging into a
        different port must Just Work on an arcade) starts feeding the combo
        engine as soon as the kernel enumerates it, without a cabd restart.
        """
        import asyncio
        from evdev import ecodes

        held_counts = {}

        async def reader(dev):
            held = set()

            def emit(button, down):
                # Releasing/unplugging one panel must not release a button
                # still held on the other panel.
                if down == (button in held):
                    return
                if down:
                    held.add(button)
                    held_counts[button] = held_counts.get(button, 0) + 1
                    if held_counts[button] == 1:
                        self.on_key(button, True)
                else:
                    held.remove(button)
                    held_counts[button] -= 1
                    if not held_counts[button]:
                        self.on_key(button, False)

            # Brook's generic PS4 HID descriptor numbers buttons sequentially;
            # Linux's BTN_* names do not describe their physical positions.
            brook = (dev.info.vendor, dev.info.product) == (0x0c12, 0x0c30)
            brook_buttons = dict(enumerate(
                ("y", "b", "a", "x", "l", "r", "l2", "r2",
                 "select", "start", "l3", "r3"), 304))
            hats = {ecodes.ABS_HAT0X: 0, ecodes.ABS_HAT0Y: 0}
            try:
                async for ev in dev.async_read_loop():
                    if ev.type == ecodes.EV_ABS:
                        self.on_activity()
                        if ev.code in hats:
                            names = ("left", "right") if ev.code == ecodes.ABS_HAT0X else ("up", "down")
                            old = hats[ev.code]
                            if old != ev.value:
                                if old:
                                    emit(names[old > 0], False)
                                if ev.value:
                                    emit(names[ev.value > 0], True)
                                hats[ev.code] = ev.value
                        continue
                    if ev.type != ecodes.EV_KEY or ev.value == 2:  # no repeats
                        continue
                    code = ecodes.keys.get(ev.code) or ecodes.BTN.get(ev.code)
                    btn = brook_buttons.get(ev.code) if brook else resolve_button(code)
                    self.on_activity()
                    if btn:
                        emit(btn, ev.value == 1)
            except OSError:
                pass   # device unplugged; the rescan loop drops it
            finally:
                for button in list(held):
                    emit(button, False)
                await close_device(dev)

        tasks = {}
        while True:
            await self.scan(tasks, reader)
            await asyncio.sleep(5)
