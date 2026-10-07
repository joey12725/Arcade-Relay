"""Input rescans classify /dev/input from sysfs: only wanted devices are opened."""
import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'cabinet'))
import cabd
import evdev
import inputs
import panels

BROOK = (0x0c12, 0x0c30)
LEFT, RIGHT = 'usb-0000:00:14.0-8.4.1/input0', 'usb-0000:00:14.0-8.4.4/input0'


class FakeDevice:
    fd = -1

    def __init__(self, path, vendor, product, phys, ev, events=()):
        self.path, self.phys, self.ev, self.events = path, phys, ev, events
        self.info = SimpleNamespace(vendor=vendor, product=product)
        self.keys, self.abs = [], {}
        self.closed = threading.Event()

    def capabilities(self):
        return {bit: [] for bit in range(32) if self.ev >> bit & 1}

    def grab(self):
        pass

    def active_keys(self):
        return self.keys

    def absinfo(self, code):
        return SimpleNamespace(value=self.abs.get(code, 0))

    async def async_read_loop(self):
        for kind, code, value in self.events:
            yield SimpleNamespace(type=kind, code=code, value=value)
        raise OSError('unplugged')

    def close(self):
        self.closed.set()


class FakeInput:
    """/sys/class/input/eventN links to per-plug inputNN dirs, like the kernel."""

    def __init__(self, root):
        self.root = Path(root)
        self.cls = self.root / 'class/input'
        self.cls.mkdir(parents=True)
        self.nodes, self.opened, self.serial = {}, [], 0

    def write(self, n, vendor, product, phys, ev):
        dev = (self.cls / f'event{n}').resolve() / 'device'
        for name, text in (('id/vendor', f'{vendor:04x}'), ('id/product', f'{product:04x}'),
                           ('phys', phys), ('name', 'fake'), ('capabilities/ev', f'{ev:x}')):
            (dev / name).parent.mkdir(parents=True, exist_ok=True)
            (dev / name).write_text(text + '\n')
        self.nodes[f'/dev/input/event{n}'] = (vendor, product, phys, ev)

    def plug(self, n, vendor, product, phys='', ev=0x1b):
        self.serial += 1
        node = self.root / f'devices/input{self.serial}/event{n}'
        node.mkdir(parents=True)
        (node / 'device').symlink_to('..')
        link = self.cls / f'event{n}'
        if link.is_symlink():
            link.unlink()
        link.symlink_to(node)
        self.write(n, vendor, product, phys, ev)

    def open(self, path):
        self.opened.append(path)
        return FakeDevice(path, *self.nodes[path])


class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = self.fake = FakeInput(tmp.name)
        f.plug(0, 0x046d, 0xc31d, 'usb-0000:00:14.0-8.2/input0', 0x120013)   # keyboard
        f.plug(6, 0x001f, 0x0001, 'isa0061/input0', 0x40001)                 # PC speaker
        f.plug(10, 0, 0, 'ALSA', 0x21)                                       # HDA jack
        f.plug(15, *BROOK, LEFT)
        f.plug(17, panels.VENDOR, 1, 'arcade-panel/player1', 0x20000b)
        f.plug(19, *BROOK, 'usb-0000:00:14.0-3/input0')                      # uncalibrated
        for p in (patch.object(inputs, 'SYSFS_INPUT', str(f.cls)),
                  patch.object(evdev, 'list_devices', lambda: list(f.nodes)),
                  patch.object(evdev, 'InputDevice', f.open)):
            p.start()
            self.addCleanup(p.stop)
        self.hold = asyncio.Event()
        self.attached, self.logs, self.tasks = [], [], {}
        self.bridge = panels.PanelBridge([], None, None, None, log=self.logs.append)
        self.bridge.panels = {p['player']: SimpleNamespace(profile=p)
                              for p in self.bridge.config['panels']}

    async def asyncTearDown(self):
        self.hold.set()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def read(self, dev, panel):
        self.attached.append((dev.path, panel.profile['player']))
        await self.hold.wait()

    async def reader(self, dev):
        await self.hold.wait()

    def uncalibrated(self):
        return [line for line in self.logs if 'uncalibrated' in line]

    async def test_only_brooks_on_calibrated_ports_are_opened(self):
        await self.bridge.scan(self.tasks, self.read)
        await asyncio.sleep(0)
        self.assertEqual(self.fake.opened, ['/dev/input/event15'])
        self.assertEqual(self.attached, [('/dev/input/event15', 1)])
        self.assertEqual(self.uncalibrated(), ['Brook panel on uncalibrated port: usb-0000:00:14.0-3/input0'])

    async def test_rejected_nodes_are_remembered_until_replugged(self):
        await self.bridge.scan(self.tasks, self.read)
        # Same device instance, contents now look like a calibrated Brook:
        # remembered by identity, so neither opened nor logged again.
        self.fake.write(6, *BROOK, RIGHT, 0x1b)
        await self.bridge.scan(self.tasks, self.read)
        self.assertEqual(self.fake.opened, ['/dev/input/event15'])
        self.assertEqual(len(self.uncalibrated()), 1)
        # A new device instance at those paths is looked at afresh.
        self.fake.plug(6, *BROOK, RIGHT)
        self.fake.plug(19, *BROOK, 'usb-0000:00:14.0-3/input0')
        await self.bridge.scan(self.tasks, self.read)
        await asyncio.sleep(0)
        self.assertEqual(self.fake.opened, ['/dev/input/event15', '/dev/input/event6'])
        self.assertEqual(self.attached[-1], ('/dev/input/event6', 2))
        self.assertEqual(len(self.uncalibrated()), 2)

    async def test_device_swapped_after_sysfs_read_is_closed(self):
        opened = []
        def swapped(path):
            dev = FakeDevice(path, 0x046d, 0xc31d, 'usb-0000:00:14.0-8.4.1/input1', 0x1b)
            opened.append(dev)
            return dev
        with patch.object(evdev, 'InputDevice', swapped):
            await self.bridge.scan(self.tasks, self.read)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed.is_set())
        self.assertFalse(self.tasks)

    async def test_incomplete_sysfs_is_retried(self):
        os.remove(self.fake.cls / 'event15/device/capabilities/ev')
        await self.bridge.scan(self.tasks, self.read)
        self.assertEqual(self.fake.opened, [])
        self.fake.write(15, *BROOK, LEFT, 0x1b)
        await self.bridge.scan(self.tasks, self.read)
        self.assertEqual(self.fake.opened, ['/dev/input/event15'])

    async def test_evdev_source_skips_managed_and_keyless_nodes_unopened(self):
        src = inputs.EvdevSource(None, None, ignore_device=panels.PanelBridge.is_managed)
        self.assertEqual(src.open_devices(), ['/dev/input/event0'])
        self.assertEqual(self.fake.opened, [])
        await src.scan(self.tasks, self.reader)
        self.fake.write(10, 0, 0, 'ALSA', 0x23)   # same instance: still rejected
        await src.scan(self.tasks, self.reader)
        self.assertEqual(self.fake.opened, ['/dev/input/event0'])
        self.assertEqual(src.devices, ['/dev/input/event0'])
        self.fake.plug(10, 0x046d, 0xc52b, 'usb-0000:00:14.0-1/input0', 0x120013)
        await src.scan(self.tasks, self.reader)
        self.assertEqual(self.fake.opened, ['/dev/input/event0', '/dev/input/event10'])

    async def test_evdev_source_reads_brooks_when_no_bridge_owns_them(self):
        src = inputs.EvdevSource(None, None)
        await src.scan(self.tasks, self.reader)
        self.assertEqual(self.fake.opened, ['/dev/input/event0', '/dev/input/event15',
                                            '/dev/input/event17', '/dev/input/event19'])


class ResyncTests(unittest.IsolatedAsyncioTestCase):
    def make(self, player=1):
        events, monitor = [], []
        config = json.loads(panels.PROFILE.read_text())
        panel = panels.Panel(config['panels'][player - 1], config['buttons'],
                             lambda *e: events.append(e), None,
                             lambda *e: monitor.append(e), now=lambda: 0.)
        return panel, events, monitor

    async def test_resync_releases_stuck_and_presses_missing(self):
        panel, events, monitor = self.make()
        panel.event(1, 304, 1); panel.event(1, 307, 1); panel.event(3, 16, 1)
        events.clear(); monitor.clear()
        panel.resync([307, 309], {16: 1, 17: -1})
        self.assertEqual(events, [(1, 304, 0), (1, 309, 1), (3, 17, 1)])
        self.assertEqual(monitor, [(1, 'button1', False), (1, 'button3', True)])
        self.assertEqual(set(panel.engine.held), {'button2', 'button3'})
        events.clear()
        panel.resync([307, 309], {16: 1, 17: -1})
        self.assertEqual(events, [])

    async def test_reader_resyncs_after_syn_dropped_and_closes_off_loop(self):
        writes = []

        class Pad:
            def __init__(self, caps, name, **kw): self.name = name
            def write(self, kind, code, value): writes.append((self.name, kind, code, value))
            def syn(self): pass
            def close(self): pass

        # button1 down, overflow (button5 press arrives mid-packet and is
        # skipped), then the device really has button5 down, button1 up.
        dev = FakeDevice('/dev/input/event15', *BROOK, LEFT, 0x1b,
                         [(1, 304, 1), (0, 0, 0), (0, 3, 0), (1, 305, 1), (0, 0, 0)])
        dev.keys, dev.abs = [305], {17: 1}
        loop_thread = threading.get_ident()
        close_thread = []
        close = dev.close
        dev.close = lambda: (close_thread.append(threading.get_ident()), close())
        logs = []
        bridge = panels.PanelBridge([], None, lambda *a: None, lambda: None, log=logs.append)
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeInput(tmp)
            fake.plug(15, *BROOK, LEFT)
            with patch.object(inputs, 'SYSFS_INPUT', str(fake.cls)), \
                 patch.object(evdev, 'list_devices', lambda: list(fake.nodes)), \
                 patch.object(evdev, 'InputDevice', lambda path: dev), \
                 patch.object(evdev, 'UInput', Pad):
                task = asyncio.create_task(bridge.run())
                self.assertTrue(await asyncio.to_thread(dev.closed.wait, 2))
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        left = [w[1:] for w in writes if w[0] == 'Arcade Left Panel']
        self.assertEqual(left, [(3, 16, 0), (3, 17, 0),          # attach reset
                                (1, 304, 1),
                                (1, 304, 0), (1, 305, 1), (3, 17, -1),   # resync
                                (1, 305, 0), (3, 16, 0), (3, 17, 0)])    # detach reset
        self.assertEqual(len(close_thread), 1)
        self.assertNotEqual(close_thread[0], loop_thread)


class WatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def run_watchdog(self, alive):
        threads = {}

        class Session:
            probe = True
            def alive(self):
                threads['alive'] = threading.get_ident()
                return alive

        def probe():
            threads['probe'] = threading.get_ident()
            return True

        with patch.dict(cabd.game, session=Session(), rom='x'), patch.dict(cabd.ss, on=False), \
             patch.object(cabd.launcher, 'probe_responsive', probe), \
             patch.object(cabd.launcher, 'focus_frontend') as focus, \
             patch.object(cabd.launcher, 'WATCHDOG_POLL_S', .01):
            task = asyncio.create_task(cabd.watchdog_loop())
            for _ in range(200):
                await asyncio.sleep(.01)
                if len(threads) == 2 or cabd.game['session'] is None:
                    break
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return threads, cabd.game['session'], focus

    async def test_liveness_checks_run_off_the_loop(self):
        threads, session, focus = await self.run_watchdog(alive=True)
        self.assertEqual(set(threads), {'alive', 'probe'})
        self.assertNotIn(threading.get_ident(), threads.values())
        self.assertIsNotNone(session)
        focus.assert_not_called()

    async def test_dead_game_still_recovers(self):
        threads, session, focus = await self.run_watchdog(alive=False)
        self.assertNotEqual(threads['alive'], threading.get_ident())
        self.assertIsNone(session)
        focus.assert_called_once()


if __name__ == '__main__':
    unittest.main()
