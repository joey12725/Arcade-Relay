"""Pegasus must stay out of the way for the whole cabinet game session."""
import importlib.machinery
import importlib.util
import os
import sys
import unittest
from unittest.mock import patch

path = os.path.join(os.path.dirname(__file__), '..', 'cabinet', 'cabd-launch')
loader = importlib.machinery.SourceFileLoader('cabd_launch', path)
spec = importlib.util.spec_from_loader(loader.name, loader)
shim = importlib.util.module_from_spec(spec)
loader.exec_module(shim)


class LaunchWaitTests(unittest.TestCase):
    def run_launch(self, wait):
        states = [dict(ok=True, result=dict(running=rom))
                  for rom in ('nes/game.nes', 'nes/game.nes', None)]
        argv = ['cabd-launch'] + (['--wait'] if wait else []) + ['nes', '/roms/nes/game.nes']
        with patch.object(sys, 'argv', argv), patch.object(shim, 'request', side_effect=states) as rpc, \
             patch.object(shim.time, 'sleep'), patch('builtins.print'):
            with self.assertRaises(SystemExit) as exit:
                shim.main()
            self.assertEqual(exit.exception.code, 0)
            self.assertEqual(rpc.call_args_list[0].args[0]['method'], 'game.launch')
            return rpc.call_args_list

    def test_pegasus_waits_until_the_game_is_gone(self):
        calls = self.run_launch(True)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1].args[0]['method'], 'game.running')

    def test_existing_cli_launch_still_returns_immediately(self):
        self.assertEqual(len(self.run_launch(False)), 1)


if __name__ == '__main__': unittest.main()
