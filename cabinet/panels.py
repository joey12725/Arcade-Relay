"""Calibrated, persistent player pads for the cabinet's two Brook panels.

Physical ports identify players; virtual pads remain present on unplug so
RetroArch never swaps players. Chords are evaluated separately per panel and
Start/Select are emitted on that player's pad, not an unused third controller.
"""
import asyncio
import copy
import json
from pathlib import Path
import time

from inputs import BindingEngine

VENDOR = 0xf055
PROFILE = Path(__file__).parent / 'provision/controllers/panels.json'


class Panel:
    def __init__(self, profile, button_codes, write, action, monitor, now=time.monotonic):
        self.profile = profile
        self.codes = {int(code): f'button{number}' for number, code in button_codes.items()}
        self.write = write
        self.action = action
        self.monitor = monitor
        self.now = now
        self.down = set()
        self.pulses = {}
        self.engine = BindingEngine([], self.fire, now=now)
        self.binding_entries = None

    def configure(self, entries):
        # ROM deliveries sync the whole state. An unchanged binding must
        # not erase an in-progress Start/Select/Home hold.
        if entries == self.binding_entries:
            return
        self.binding_entries = copy.deepcopy(entries)
        self.engine = BindingEngine(entries, self.fire, now=self.now)

    def fire(self, target, kind):
        if kind == 'action':
            self.action(target)
        elif target in ('start', 'select'):
            code = 313 if target == 'start' else 312
            self.pulses[code] = self.now() + .12
            self.write(1, code, 1)

    def event(self, kind, code, value):
        if kind == 1 and value != 2:
            if value:
                self.down.add(code)
            else:
                self.down.discard(code)
            self.write(kind, code, int(bool(value or code in self.pulses)))
            if code in self.codes:
                button = self.codes[code]
                self.monitor(self.profile['player'], button, bool(value))
                self.engine.key_event(button, bool(value))
        elif kind == 3 and str(code) in self.profile['axes']:
            output_code, sign = self.profile['axes'][str(code)]
            self.write(kind, output_code, value * sign)

    def poll(self):
        self.engine.poll()
        for code, deadline in list(self.pulses.items()):
            if self.now() >= deadline:
                self.pulses.pop(code)
                self.write(1, code, int(code in self.down))

    def reset(self):
        for code in self.down | self.pulses.keys():
            self.write(1, code, 0)
        self.down.clear()
        self.pulses.clear()
        self.engine.held.clear()
        self.engine.latched.clear()
        self.write(3, 16, 0)
        self.write(3, 17, 0)


class PanelBridge:
    def __init__(self, entries, action, monitor, activity, log=print):
        self.config = json.loads(PROFILE.read_text())
        self.entries = entries
        self.action, self.monitor, self.activity, self.log = action, monitor, activity, log
        self.panels = {}

    def configure(self, entries):
        # Only numbered-panel chords belong here. Generic bindings keep
        # their existing path through the cabinet's normal input reader.
        self.entries = [e for e in entries
                        if all(b.startswith('button') for b in
                               (e.get('buttons', '').split('+') if isinstance(e.get('buttons'), str)
                                else e.get('buttons', [])))]
        for panel in self.panels.values():
            panel.configure(self.entries)

    @staticmethod
    def is_managed(dev):
        return (dev.info.vendor, dev.info.product) == (0x0c12, 0x0c30) or dev.info.vendor == VENDOR

    async def run(self):
        import evdev
        from evdev import ecodes as e
        self.panels = {}
        outputs, readers = {}, {}
        # SDL 2.0.20's fallback classifier requires X/Y even for a digital
        # hat-only pad. Keep these neutral; calibrated movement uses the hat.
        caps = {e.EV_KEY: list(range(304, 318)),
                e.EV_ABS: [(code, evdev.AbsInfo(0, -1, 1, 0, 0, 0)) for code in (0, 1, 16, 17)]}
        try:
            for profile in self.config['panels']:
                player = profile['player']
                pad = evdev.UInput(caps, name=profile['name'], vendor=VENDOR,
                                  product=player, version=0x111, bustype=e.BUS_USB,
                                  phys=f'arcade-panel/player{player}')
                outputs[player] = pad
                def write(kind, code, value, pad=pad):
                    pad.write(kind, code, value)
                    pad.syn()
                self.panels[player] = Panel(profile, self.config['buttons'], write,
                                            self.action, self.monitor)
            self.configure(self.entries)
            self.log('calibrated player pads ready')

            async def read(dev, panel):
                try:
                    dev.grab()
                    panel.reset()
                    self.log(f"player {panel.profile['player']} panel attached: {dev.phys}")
                    async for event in dev.async_read_loop():
                        if event.type in (e.EV_KEY, e.EV_ABS):
                            self.activity()
                            panel.event(event.type, event.code, event.value)
                except OSError as exc:
                    self.log(f"panel detached: {dev.path}: {exc}")
                finally:
                    panel.reset()
                    dev.close()

            next_scan = 0
            while True:
                if time.monotonic() >= next_scan:
                    for path, task in list(readers.items()):
                        if task.done():
                            task.result()
                            del readers[path]
                    for path in sorted(evdev.list_devices()):
                        if path in readers:
                            continue
                        try:
                            dev = evdev.InputDevice(path)
                            if (dev.info.vendor, dev.info.product) != (self.config['vendor'], self.config['product']):
                                dev.close()
                                continue
                            panel = next((p for p in self.panels.values()
                                          if dev.phys.endswith('-' + p.profile['usb_port'] + '/input0')), None)
                            if panel:
                                readers[path] = asyncio.create_task(read(dev, panel))
                            else:
                                self.log(f'Brook panel on uncalibrated port: {dev.phys}')
                                dev.close()
                        except OSError:
                            continue
                    next_scan = time.monotonic() + 2
                for panel in self.panels.values():
                    panel.poll()
                await asyncio.sleep(.01)
        finally:
            for task in readers.values():
                task.cancel()
            await asyncio.gather(*readers.values(), return_exceptions=True)
            for pad in outputs.values():
                pad.close()
