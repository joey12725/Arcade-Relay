"""Library deliveries must not bring the menu over an active game."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'cabinet'))
import cabd


class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def tick(self):
        calls = 0
        async def sleep(_):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise asyncio.CancelledError()
        with patch.object(cabd.asyncio, 'sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await cabd.frontend_reload_loop()

    async def test_download_during_game_defers_then_reloads_once_at_home(self):
        state = {'proc': None, 'reload_pending': True, 'last_rescan': 0}
        game = {'session': object()}
        restart = AsyncMock()
        with patch.object(cabd, 'frontend', state), patch.object(cabd, 'game', game), \
             patch.object(cabd, 'game_lock', asyncio.Lock()), \
             patch.object(cabd, 'frontend_restart', restart):
            await self.tick()
            restart.assert_not_awaited()
            self.assertTrue(state['reload_pending'])
            game['session'] = None
            await self.tick()
            restart.assert_awaited_once()
            await self.tick()
            restart.assert_awaited_once()

    async def test_batch_of_deliveries_waits_for_quiet_period(self):
        with patch.object(cabd, 'frontend', {'reload_pending': True,
                                           'last_rescan': cabd.time.monotonic()}), \
             patch.object(cabd, 'game', {'session': None}), \
             patch.object(cabd, 'game_lock', asyncio.Lock()), \
             patch.object(cabd, 'frontend_restart', AsyncMock()) as restart:
            await self.tick()
            restart.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
