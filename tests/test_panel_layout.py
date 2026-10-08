"""Panel layouts: one resolver over base, global, built-in system, system and
game layers; rendered per player as RetroArch autoconfig."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'cabinet'))
import panel_layout as pl
import renderer

PROFILE = pl.load_profile()
CONTROLLERS = Path(__file__).parent.parent / 'cabinet/provision/controllers'
# controllers/* of the live state bundle, 2026-10-07: the web remapper's
# player1.button1-8 global entries plus the sim fixtures (a: b, ...).
LIVE = json.loads((Path(__file__).parent /
                   'fixtures/live-controllers-2026-10-07.json').read_text())
SNES_STYLE = ['nes', 'snes', 'gb', 'gba', 'psx', 'unknown-system']


def layouts(system, rom=None, glob=None, sysmap=None, games=None):
    bundle = {'controllers/global.yaml': {'buttons': glob or {}},
              f'controllers/systems/{system}.yaml': {'remap': sysmap or {}},
              'controllers/games.yaml': {'games': games or {}}}
    return pl.effective_layouts(bundle, system, rom)


def without(layout, *numbers):
    return {n: t for n, t in layout.items() if n not in numbers}


def parse(text):
    return dict(l.split(' = ') for l in text.splitlines() if ' = ' in l)


def binds(text):
    """{target: RetroArch button index} from autoconfig text."""
    return {k[6:-4]: int(v.strip('"')) for k, v in parse(text).items()
            if k.endswith('_btn') and not v.startswith('"h0')}


class ResolverTests(unittest.TestCase):
    def test_snes_style_systems_get_the_nintendo_base_for_both_players(self):
        for system in SNES_STYLE:
            self.assertEqual(layouts(system), {1: pl.NINTENDO, 2: pl.NINTENDO})

    def test_genesis_and_arcade_builtins(self):
        g, a = layouts('genesis')[1], layouts('arcade')[1]
        # Genesis A B C = RetroPad Y B A on panels 1 2 3, X Y Z on 4 5 6
        self.assertEqual([g[n] for n in range(1, 7)], ['y', 'b', 'a', 'l', 'x', 'r'])
        # MAME Button 1-6 = RetroPad B A Y X L R
        self.assertEqual([a[n] for n in range(1, 7)], ['b', 'a', 'y', 'x', 'l', 'r'])

    def test_start_7_select_8_everywhere(self):
        for system in SNES_STYLE + ['genesis', 'arcade']:
            for layout in layouts(system).values():
                self.assertEqual((layout[7], layout[8]), ('start', 'select'), system)

    def test_partial_override_moves_the_target_and_frees_the_old_button(self):
        got = layouts('snes', glob={'button2': 'a'})[1]
        # a leaves panel 1; panel 2's y is displaced, not doubled up
        self.assertEqual(got, {2: 'a', **without(pl.NINTENDO, 1, 2)})

    def test_two_entries_swap_cleanly(self):
        got = layouts('snes', glob={'button1': 'y', 'button2': 'a'})[1]
        self.assertEqual(got, {**pl.NINTENDO, 1: 'y', 2: 'a'})

    def test_no_target_is_ever_on_two_buttons(self):
        got = layouts('gba', glob={'button1': 'b', 'button2': 'b', 'button3': 'b'},
                      sysmap={'player1.button4': 'start'})[1]
        self.assertEqual(len(got), len(set(got.values())))
        self.assertEqual((got[3], got[4]), ('b', 'start'))
        self.assertNotIn(7, got)

    def test_both_player_and_per_player_keys(self):
        got = layouts('snes', glob={'button3': 'l2', 'player2.button3': 'r2',
                                    'player1.button6': 'r2'})
        self.assertEqual((got[1][3], got[1][6]), ('l2', 'r2'))
        self.assertEqual((got[2][3], got[2][6]), ('r2', 'x'))

    def test_own_key_on_a_panel_replaces_the_both_player_one_outright(self):
        # button2: x must not take x off panel 6 for player 1 first
        got = layouts('snes', glob={'button2': 'x', 'player1.button2': 'y'})
        self.assertEqual(got[1], pl.NINTENDO)
        self.assertEqual(got[2], {**without(pl.NINTENDO, 6), 2: 'x'})

    def test_a_layers_changing_entry_beats_one_restating_the_layout_below(self):
        full = {f'player1.button{n}': t for n, t in pl.NINTENDO.items()}
        for key, target, panel, freed in (('player1.button2', 'x', 2, 6),
                                          ('player1.button1', 'b', 1, 5),
                                          ('player1.button6', 'y', 6, 2)):
            got = layouts('snes', glob={**full, key: target})[1]
            self.assertEqual(got, {**without(pl.NINTENDO, freed), panel: target}, key)

    def test_per_player_key_leaves_the_other_player_alone(self):
        got = layouts('snes', sysmap={'player2.button1': 'b'})
        self.assertEqual(got[1], pl.NINTENDO)
        self.assertEqual(got[2], {**without(pl.NINTENDO, 5), 1: 'b'})

    def test_precedence_across_all_five_layers(self):
        rom, other = '/roms/snes/smw.sfc', '/roms/snes/other.sfc'
        g = {'button3': 'l2'}
        s = {'button3': 'r2'}
        games = {'snes/smw.sfc': {'remap': {'button3': 'select'}},
                 'genesis/sonic.md': {'remap': {'button3': 'select'}}}
        self.assertEqual(layouts('snes')[1][3], 'l')                    # base
        self.assertEqual(layouts('snes', glob=g)[1][3], 'l2')           # global
        self.assertEqual(layouts('genesis', glob=g)[1][3], 'a')         # built-in
        self.assertEqual(layouts('snes', glob=g, sysmap=s)[1][3], 'r2')     # system
        self.assertEqual(layouts('genesis', glob=g, sysmap=s)[1][3], 'r2')
        self.assertEqual(layouts('snes', rom, g, s, games)[1][3], 'select')  # game
        self.assertEqual(layouts('genesis', '/roms/genesis/sonic.md', g, s,
                                 games)[1][3], 'select')
        self.assertEqual(layouts('snes', other, g, s, games)[1][3], 'r2')
        self.assertEqual(layouts('snes', None, g, s, games)[1][3], 'r2')

    def test_global_entries_do_not_reach_genesis_or_arcade(self):
        glob = {f'button{n}': t for n, t in pl.NINTENDO.items()}
        self.assertEqual(layouts('genesis', glob=glob)[1], pl.SYSTEM_LAYOUTS['genesis'])
        self.assertEqual(layouts('arcade', glob=glob)[2], pl.SYSTEM_LAYOUTS['arcade'])

    def test_legacy_and_malformed_keys_are_ignored(self):
        junk = {'a': 'b', 'b': 'a', 'start': 'start', 'x': 'y', 'button9': 'a',
                'button0': 'a', 'player3.button1': 'b', 'player1.button10': 'b',
                'player1button1': 'b', 'player1.button1.': 'b', 1: 'b'}
        self.assertEqual(layouts('snes', glob=junk, sysmap=junk), {1: pl.NINTENDO, 2: pl.NINTENDO})

    def test_invalid_values_are_ignored(self):
        bad = {'button1': 'up', 'button2': 'cross', 'button3': True,
               'button4': 3, 'button5': None, 'button6': 'l3', 'button7': ''}
        self.assertEqual(layouts('snes', glob=bad)[1], pl.NINTENDO)
        self.assertEqual(layouts('snes', glob={'button3': 'L2'})[1][3], 'l2')

    def test_missing_or_odd_layer_files(self):
        self.assertEqual(pl.effective_layouts({}, 'snes', '/roms/snes/x.sfc'),
                         {1: pl.NINTENDO, 2: pl.NINTENDO})
        bundle = {'controllers/global.yaml': {'buttons': None},
                  'controllers/systems/snes.yaml': {'remap': ['a']},
                  'controllers/games.yaml': {'games': {'snes/x.sfc': None}}}
        self.assertEqual(pl.effective_layouts(bundle, 'snes', '/roms/snes/x.sfc')[1],
                         pl.NINTENDO)


class AutoconfigTests(unittest.TestCase):
    def text(self, layout, panel=0):
        return '\n'.join(pl.autoconfig(PROFILE['panels'][panel], layout, PROFILE))

    def test_panel_numbers_become_the_recorded_evdev_indexes(self):
        # panel 1 = 304 (index 0), 5 = 305 (1), 6 = 306 (2), 2 = 307 (3),
        # 4 = 308 (4), 3 = 309 (5), 8 = 310 (6), 7 = 311 (7)
        self.assertEqual(binds(self.text(pl.NINTENDO)), {
            'a': 0, 'b': 1, 'x': 2, 'y': 3, 'l': 5, 'r': 4,
            'start': 7, 'select': 6})

    def test_unbound_targets_are_omitted_and_dpad_stays_on_the_hat(self):
        cfg = parse(self.text({2: 'a', 3: 'l2'}))
        self.assertEqual(binds(self.text({2: 'a', 3: 'l2'})), {'a': 3, 'l2': 5})
        for d in ('up', 'down', 'left', 'right'):
            self.assertEqual(cfg[f'input_{d}_btn'], f'"h0{d}"')

    def test_each_panel_profile_names_its_own_device(self):
        for i, panel in enumerate(PROFILE['panels']):
            cfg = parse(self.text(pl.NINTENDO, i))
            self.assertEqual(cfg['input_device'], f'"{panel["name"]}"')
            self.assertEqual(cfg['input_product_id'], f'"{panel["player"]}"')
            self.assertEqual(cfg['input_vendor_id'], '"61525"')

    def test_installed_profiles_match_the_default_layout(self):
        for i, panel in enumerate(PROFILE['panels']):
            name = panel['name'].replace(' ', '_') + '.cfg'
            self.assertEqual((CONTROLLERS / name).read_text(),
                             self.text(pl.NINTENDO, i) + '\n', name)

    def test_pegasus_menu_map_follows_the_nintendo_layout(self):
        env = (CONTROLLERS / 'pegasus.env').read_text()
        index = {int(n): c - pl.BASE_CODE for n, c in PROFILE['buttons'].items()}
        sdl = {'a': 'a', 'b': 'b', 'x': 'x', 'y': 'y', 'l': 'leftshoulder',
               'r': 'rightshoulder', 'start': 'start', 'select': 'back'}
        want = ','.join(f'{sdl[t]}:b{index[n]}' for n, t in sorted(pl.NINTENDO.items()))
        for panel in PROFILE['panels']:
            line = next(l for l in env.splitlines() if f',{panel["name"]},' in l)
            got = sorted(f for f in line.split(',') if f.split(':')[0] in sdl.values())
            self.assertEqual(got, sorted(want.split(',')), panel['name'])
            self.assertNotRegex(line, r'trigger|:[+-]?a\d')


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stock = os.path.join(self.tmp.name, 'stock')
        os.makedirs(self.stock)
        for name in ('Arcade_Left_Panel.cfg', 'Some Other Pad.cfg', 'Gone Pad.cfg'):
            Path(self.stock, name).write_text('stock\n')
        old = renderer.RENDER_DIR
        renderer.RENDER_DIR = os.path.join(self.tmp.name, 'rendered')
        self.addCleanup(setattr, renderer, 'RENDER_DIR', old)
        self.out = os.path.join(renderer.RENDER_DIR, 'autoconfig')

    def bundle(self, stock=None, glob=None, games=None, **systems):
        b = {'cabinet.yaml': {'retroarch': {
                 'joypad_autoconfig_dir': stock or self.stock}},
             'controllers/global.yaml': glob or {},
             'controllers/games.yaml': games or {}}
        b.update({f'controllers/systems/{s}.yaml': v for s, v in systems.items()})
        return b

    def panel(self, side='Left'):
        return Path(self.out, f'Arcade_{side}_Panel.cfg').read_text()

    def test_launch_renders_both_panels_and_points_retroarch_at_them(self):
        renderer.render(self.bundle(), 'gba')
        self.assertEqual(binds(self.panel()), binds(self.panel('Right')))
        self.assertIn('input_start_btn = "7"', self.panel())
        self.assertIn('input_device = "Arcade Right Panel"', self.panel('Right'))
        self.assertTrue(self.panel().startswith(renderer.HEADER))
        cfg = Path(renderer.RENDER_DIR, 'retroarch.cfg').read_text()
        self.assertEqual(cfg.count('joypad_autoconfig_dir'), 1)
        self.assertIn(f'joypad_autoconfig_dir = "{self.out}"', cfg)

    def test_each_player_renders_from_its_own_layout(self):
        renderer.render(self.bundle(glob={'buttons': {
            'player2.button1': 'b', 'player1.button3': 'l2'}}), 'snes')
        left, right = binds(self.panel()), binds(self.panel('Right'))
        self.assertEqual((left['a'], left['l2']), (0, 5))
        self.assertNotIn('l', left)
        self.assertEqual(right['b'], 0)
        self.assertNotIn('a', right)

    def test_game_layer_reaches_the_rendered_profile(self):
        games = {'games': {'snes/smw.sfc': {'remap': {'button4': 'r2'}}}}
        renderer.render(self.bundle(games=games), 'snes', '/roms/snes/smw.sfc')
        self.assertEqual(binds(self.panel())['r2'], 4)
        renderer.render(self.bundle(games=games), 'snes', '/roms/snes/other.sfc')
        self.assertEqual(binds(self.panel())['r'], 4)

    def test_other_pads_keep_their_stock_profiles(self):
        renderer.render(self.bundle(), 'snes')
        self.assertEqual(Path(self.out, 'Some Other Pad.cfg').read_text(), 'stock\n')
        self.assertNotEqual(self.panel(), 'stock\n')

    def test_stale_entries_cannot_break_or_shadow_the_next_render(self):
        renderer.render(self.bundle(), 'snes')
        os.unlink(os.path.join(self.stock, 'Gone Pad.cfg'))   # target vanished
        os.unlink(os.path.join(self.out, 'Arcade_Left_Panel.cfg'))
        os.symlink(os.path.join(self.stock, 'Arcade_Left_Panel.cfg'),
                   os.path.join(self.out, 'Arcade_Left_Panel.cfg'))
        os.unlink(os.path.join(self.out, 'Arcade_Right_Panel.cfg'))
        os.makedirs(os.path.join(self.out, 'Arcade_Right_Panel.cfg'))
        os.symlink(os.path.join(self.stock, 'Arcade_Left_Panel.cfg'),
                   os.path.join(self.out, 'Arcade_Left_Panel.cfg.tmp'))
        Path(self.out, 'Old Render.cfg').write_text('stale\n')
        os.makedirs(os.path.join(self.out, 'udev'))
        renderer.render(self.bundle(), 'genesis')
        self.assertEqual(sorted(os.listdir(self.out)), [
            'Arcade_Left_Panel.cfg', 'Arcade_Right_Panel.cfg', 'Some Other Pad.cfg'])
        self.assertFalse(os.path.islink(os.path.join(self.out, 'Arcade_Left_Panel.cfg')))
        self.assertEqual(Path(self.stock, 'Arcade_Left_Panel.cfg').read_text(), 'stock\n')
        self.assertEqual(binds(self.panel('Right'))['y'], 0)

    def test_a_moved_stock_dir_is_followed(self):
        renderer.render(self.bundle(), 'snes')
        moved = os.path.join(self.tmp.name, 'moved')
        os.rename(self.stock, moved)
        renderer.render(self.bundle(stock=moved), 'snes')
        self.assertEqual(os.readlink(os.path.join(self.out, 'Some Other Pad.cfg')),
                         os.path.join(moved, 'Some Other Pad.cfg'))

    def test_unset_stock_dir_falls_back_to_the_installed_one(self):
        old = renderer.STOCK_AUTOCONFIG
        renderer.STOCK_AUTOCONFIG = self.stock
        self.addCleanup(setattr, renderer, 'STOCK_AUTOCONFIG', old)
        renderer.render({}, 'snes')
        self.assertTrue(os.path.islink(os.path.join(self.out, 'Some Other Pad.cfg')))

    def test_stock_dir_pointing_at_the_render_dir_is_not_linked_to_itself(self):
        renderer.render(self.bundle(), 'snes')
        renderer.render(self.bundle(stock=self.out), 'snes')
        self.assertEqual(sorted(os.listdir(self.out)),
                         ['Arcade_Left_Panel.cfg', 'Arcade_Right_Panel.cfg'])

    def test_physical_keys_are_not_keyboard_binds_and_legacy_render_is_kept(self):
        renderer.render(self.bundle(
            glob={'buttons': {'a': 'a', 'start': 'start', 'button2': 'b',
                              'player1.button1': 'a', 'player9.button12': 'x'}},
            snes={'remap': {'a': 'b', 'b': 'a'}}), 'snes')
        cfg = Path(renderer.RENDER_DIR, 'retroarch.cfg').read_text()
        self.assertIn('input_player1_a = "a"\ninput_player1_start = "start"\n', cfg)
        self.assertNotIn('button', cfg)
        rmp = Path(renderer.RENDER_DIR, 'snes.rmp').read_text()
        self.assertEqual(rmp, f'{renderer.HEADER}\na = "b"\nb = "a"\n')


class LiveStateTests(unittest.TestCase):
    """What the cabinet renders from the live state captured above."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old = renderer.RENDER_DIR
        renderer.RENDER_DIR = self.tmp.name
        self.addCleanup(setattr, renderer, 'RENDER_DIR', old)
        self.bundle = dict(LIVE, **{'cabinet.yaml': {'retroarch': {
            'joypad_autoconfig_dir': os.path.join(self.tmp.name, 'none')}}})

    def render(self, system, side='Left'):
        renderer.render(self.bundle, system, f'/roms/{system}/game.rom')
        return binds(Path(self.tmp.name, 'autoconfig',
                          f'Arcade_{side}_Panel.cfg').read_text())

    def test_snes_is_what_the_user_mapped(self):
        # panels 1 5 6 2 3 4 7 8 = a b x y l r start select; the {a: b, b: a}
        # sim fixture does not swap A/B
        want = {'a': 0, 'b': 1, 'x': 2, 'y': 3, 'l': 5, 'r': 4,
                'start': 7, 'select': 6}
        self.assertEqual(self.render('snes'), want)
        self.assertEqual(self.render('snes', 'Right'), want)

    def test_sim_fixtures_are_ignored(self):
        for system in ('nes', 'gba', 'psx', 'gb'):
            self.assertEqual(self.render(system), self.render('snes'), system)

    def test_genesis_keeps_a_b_c_on_1_2_3(self):
        want = {'y': 0, 'b': 3, 'a': 5, 'l': 4, 'x': 1, 'r': 2,
                'start': 7, 'select': 6}
        self.assertEqual(self.render('genesis'), want)
        self.assertEqual(self.render('genesis', 'Right'), want)

    def test_arcade_buttons_1_to_6_in_order(self):
        want = {'b': 0, 'a': 3, 'y': 5, 'x': 4, 'l': 1, 'r': 2,
                'start': 7, 'select': 6}
        self.assertEqual(self.render('arcade'), want)
        self.assertEqual(self.render('arcade', 'Right'), want)

    def test_a_remap_merged_beside_the_old_capture_takes_effect(self):
        # what an older /controls/map (plain dict.update) stored for "press
        # panel 2, pick X": player1.button2: x next to player1.button6: x
        glob = self.bundle['controllers/global.yaml']
        self.bundle['controllers/global.yaml'] = dict(
            glob, buttons=dict(glob['buttons'], **{'player1.button2': 'x'}))
        got = self.render('snes')
        self.assertEqual(got['x'], 3)
        self.assertNotIn('y', got)
        self.assertEqual(self.render('snes', 'Right')['x'], 2)

    def test_no_junk_keyboard_binds(self):
        self.render('snes')
        cfg = Path(self.tmp.name, 'retroarch.cfg').read_text()
        self.assertNotIn('button', cfg)
        self.assertIn('input_player1_a = "a"', cfg)


if __name__ == '__main__':
    unittest.main()
