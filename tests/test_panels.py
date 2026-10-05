"""Recorded panel calibration and player-local hold combinations."""
import json
import os
import sys
import unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'cabinet'))
from panels import Panel, PROFILE

BINDINGS = [dict(buttons=['button4', 'button8'], hold_ms=2000, target='start'),
            dict(buttons=['button3', 'button7'], hold_ms=2000, target='select'),
            dict(buttons=['button1', 'button5'], hold_ms=2000, target='home')]
CONFIG = json.loads(PROFILE.read_text())


class PanelTests(unittest.TestCase):
    def make(self, player):
        events, actions, monitor, now = [], [], [], [0.]
        panel = Panel(CONFIG['panels'][player-1], CONFIG['buttons'],
                      lambda *e: events.append(e), actions.append,
                      lambda *e: monitor.append(e), now=lambda: now[0])
        panel.configure(BINDINGS)
        return panel, events, actions, monitor, now

    def test_recorded_directions(self):
        # Exact reported up/left/right/down (left) and left/right/up/down (right).
        for player, raw, expected in [
            (1, [(17,1),(16,1),(16,-1),(17,-1)], [(17,-1),(16,-1),(16,1),(17,1)]),
            (2, [(17,-1),(17,1),(16,-1),(16,1)], [(16,-1),(16,1),(17,-1),(17,1)])]:
            panel, events, _, _, _ = self.make(player)
            for code,value in raw: panel.event(3,code,value)
            self.assertEqual(events, [(3,c,v) for c,v in expected])

    def test_all_numbered_buttons_match_capture(self):
        for player in (1,2):
            panel, _, _, monitor, _ = self.make(player)
            for i,code in enumerate([304,307,309,308,305,306,311,310],1):
                panel.event(1,code,1); panel.event(1,code,0)
                self.assertEqual(monitor[-2:], [(player,f'button{i}',True),(player,f'button{i}',False)])

    def test_start_select_pulse_once_on_each_players_own_pad(self):
        for player in (1,2):
            for chord, target in [((308,310),313), ((309,311),312)]:
                panel, events, actions, _, now = self.make(player)
                panel.event(1,chord[0],1)
                now[0]=.1; panel.event(1,chord[1],1)
                now[0]=2.;panel.poll()
                self.assertNotIn((1,target,1),events)
                now[0]=2.11;panel.poll()
                self.assertEqual(events[-1],(1,target,1))
                now[0]=2.3;panel.poll()
                self.assertEqual(events[-1],(1,target,0))
                now[0]=8;panel.poll()
                self.assertEqual(events.count((1,target,1)),1)
                self.assertFalse(actions)

    def test_home_and_no_cross_player_chords(self):
        left, _, la, _, lt = self.make(1)
        right, _, ra, _, rt = self.make(2)
        left.event(1,304,1);right.event(1,305,1)
        lt[0]=rt[0]=3;left.poll();right.poll()
        self.assertFalse(la or ra)
        left.event(1,305,1);lt[0]=5.1;left.poll()
        self.assertEqual(la,['home']);self.assertFalse(ra)

    def test_release_before_threshold_and_disconnect(self):
        p, events, actions, _, now = self.make(1)
        p.event(1,308,1);p.event(1,310,1)
        now[0]=1;p.event(1,308,0)
        now[0]=3;p.poll()
        self.assertNotIn((1,313,1),events)
        p.event(3,16,1);p.reset()
        self.assertEqual(events[-2:],[(3,16,0),(3,17,0)])
        self.assertEqual(p.down,set());self.assertFalse(p.engine.held)


if __name__ == '__main__': unittest.main()
