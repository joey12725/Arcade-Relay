"""Refocusing the menu must land on Pegasus's real window, never a Qt helper."""
import os
import re
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'cabinet'))
import launcher

UNIT = os.path.join(os.path.dirname(__file__), '..', 'cabinet', 'provision',
                    'systemd', 'cabd.service')

# What `xdotool search --name` walks on the cabinet, in its breadth-first
# order: Qt's unmapped helpers are direct children of the root, so they come
# before the real windows, which sit one level down inside Openbox frames.
WINDOWS = [
    ('0x600004', 'Qt Selection Owner for pegasus-fe'),
    ('0x600012', 'pegasus-frontend'),
    ('0x600010', 'Pegasus'),
    ('0x800002', 'RetroArch Nestopia 1.53.0'),
]


def setUpModule():
    # every X call below is faked; never let a stray one reach a live display
    os.environ.pop('DISPLAY', None)


def unit_frontend_title():
    with open(UNIT) as f:
        for line in f:
            if line.startswith('Environment=FRONTEND_TITLE='):
                return line.strip().split('=', 2)[2]
    raise AssertionError('cabd.service sets no FRONTEND_TITLE')


class FrontendWindowTests(unittest.TestCase):
    def xdotool(self, calls):
        def run(*args):
            calls.append(args)
            out = ''
            if args[:2] == ('search', '--name'):
                # xdotool compiles --name as a case-insensitive extended regex
                out = '\n'.join(w for w, name in WINDOWS
                                if re.search(args[2], name, re.IGNORECASE))
            return SimpleNamespace(stdout=out)
        return run

    def test_unit_title_matches_only_the_real_window(self):
        calls = []
        with patch.object(launcher, '_xdotool', self.xdotool(calls)):
            self.assertEqual(launcher.find_window(unit_frontend_title()), '0x600010')

    def test_refocus_never_maps_a_helper_window(self):
        calls = []
        with patch.object(launcher, '_xdotool', self.xdotool(calls)), \
             patch.object(launcher, 'FRONTEND_TITLE', unit_frontend_title()):
            self.assertEqual(launcher.focus_frontend(), '0x600010')
        touched = {a[-1] for a in calls if a[0] != 'search'}
        self.assertEqual(touched, {'0x600010'})

    def test_game_launch_leaves_the_menu_mapped(self):
        calls = []
        with patch.object(launcher, '_xdotool', self.xdotool(calls)), \
             patch.object(launcher, 'FRONTEND_TITLE', unit_frontend_title()), \
             patch.object(launcher.subprocess, 'Popen') as popen:
            popen.return_value.poll.return_value = None
            session = launcher.GameSession('/roms/nes/x.nes', title='RetroArch')
        self.assertEqual(session.window, '0x800002')
        self.assertFalse([a for a in calls if a[0] == 'windowunmap'])
        self.assertEqual({a[-1] for a in calls if a[0] != 'search'}, {'0x800002'})


if __name__ == '__main__': unittest.main()
