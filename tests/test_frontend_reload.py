"""Library deliveries must not bring the menu over an active game."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'cabinet'))
import cabd


class ReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_favorites_change_queues_refresh_but_unchanged_sync_does_not(self):
        old = {'library/favorites.yaml': {'favorites': ['nes/Mario.nes']}}
        new = {'library/favorites.yaml': {'favorites': ['snes/World.sfc']}}
        frontend = {'reload_pending': False, 'last_rescan': 0}
        ws = AsyncMock()
        with patch.object(cabd, 'state', {'bundle': old}), \
             patch.object(cabd, 'frontend', frontend), \
             patch.object(cabd, 'save_state_cache'), \
             patch.object(cabd, 'build_combo_engine'), \
             patch.object(cabd.pegasus_gen, 'render_all', return_value=[]), \
             patch.object(cabd.audio, 'apply_all', return_value=[]):
            await cabd.handle_req(ws, {'method': 'state.sync', 'params': {'bundle': new}})
            self.assertTrue(ws.send_json.call_args.args[0]['ok'])
            self.assertTrue(frontend['reload_pending'])
            frontend['reload_pending'] = False
            await cabd.handle_req(ws, {'method': 'state.sync', 'params': {'bundle': new}})
            self.assertFalse(frontend['reload_pending'])

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
