"""Regression coverage for real evdev input (no physical device required)."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'cabinet'))
import inputs
import cabd
import pytest

evdev = pytest.importorskip("evdev")  # Linux-only input layer


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_brook_buttons_hats_and_unplug(self):
        events = [(3, 16, -1), (3, 17, -1), (3, 16, 1), (3, 16, 0), (3, 17, 0)]
        # All eight face/shoulder buttons, select/start and both stick clicks.
        for code in range(304, 316):
            events += [(1, code, 1), (1, code, 0)]
        events += [(1, 313, 1)]  # unplug while Start is held
        closed = asyncio.Event()
        loop = asyncio.get_running_loop()

        class Device:
            fd = -1
            info = SimpleNamespace(vendor=0x0c12, product=0x0c30)
            def capabilities(self): return {1: []}
            async def async_read_loop(self):
                for kind, code, value in events:
                    yield SimpleNamespace(type=kind, code=code, value=value)
                raise OSError('unplugged')
            def close(self): loop.call_soon_threadsafe(closed.set)   # closed off-loop

        keys, activity = [], []
        src = inputs.EvdevSource(lambda *x: keys.append(x), lambda: activity.append(1))
        sysfs = inputs.NodeInfo('input1', 0x0c12, 0x0c30, '', 'Brook', 0x1b)
        with patch.object(evdev, 'list_devices', return_value=['test-pad']), \
             patch.object(inputs, 'node_info', return_value=sysfs), \
             patch.object(evdev, 'InputDevice', return_value=Device()):
            task = asyncio.create_task(src.run())
            await asyncio.wait_for(closed.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(keys[:8], [('left', True), ('up', True), ('left', False),
                                   ('right', True), ('right', False), ('up', False),
                                   ('y', True), ('y', False)])
        self.assertEqual({b for b, down in keys if down},
                         {'left', 'right', 'up', 'a', 'b', 'x', 'y', 'l', 'r',
                          'l2', 'r2', 'select', 'start', 'l3', 'r3'})
        self.assertEqual(keys[-2:], [('start', True), ('start', False)])
        self.assertEqual(len(activity), len(events))

    async def test_physical_activity_wakes_once_even_during_start(self):
        cabd._input_wake_task = None
        with patch.dict(cabd.ss, on=False, starting=True):
            with patch.object(cabd, 'screensaver_off') as off:
                cabd._on_input_activity()
                wake = cabd._input_wake_task
                cabd._on_input_activity()
                self.assertIs(wake, cabd._input_wake_task)
                await asyncio.sleep(0)
                off.assert_not_awaited()
                cabd.ss['starting'] = False
                cabd.ss['on'] = True
                await asyncio.wait_for(wake, 1)
                off.assert_awaited_once()

    async def test_physical_keys_reach_monitor_and_engines(self):
        with patch.object(cabd, 'combo_engine') as combo, \
             patch.object(cabd, 'binding_engine') as binding:
            cabd._on_evdev_key('start', True)
            self.assertEqual(cabd.events_buf[-1][1], 'start down')
            combo.key_event.assert_called_once_with('start', True)
            binding.key_event.assert_called_once_with('start', True)


if __name__ == '__main__':
    unittest.main()
