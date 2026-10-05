import asyncio
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'cabinet'))
import ha_local


class LocalHATests(unittest.TestCase):
    def test_one_device_for_all_entities(self):
        pub = ha_local.integrations.HomeAssistantPublisher(
            {'homeassistant': {'node_id': 'arcade_cabinet'}}, lambda _: None)
        entries = pub._discovery()
        self.assertEqual(len(entries), 11)
        self.assertEqual(len({p['unique_id'] for _, p in entries}), 11)
        for _, payload in entries:
            self.assertEqual(payload['device']['identifiers'], ['arcade_cabinet'])

    def test_restart_reannounces_and_retained_commands_do_not_execute(self):
        calls = []
        pub = ha_local.integrations.HomeAssistantPublisher({}, lambda _: None,
                                                            lambda *x: calls.append(x))
        pub.client = Mock()
        pub._announced = True
        pub._on_connect(pub.client, None, {}, 0)
        self.assertFalse(pub._announced)
        pub.announce()
        self.assertTrue(pub._announced)
        pub._on_message(None, None, types.SimpleNamespace(
            topic=pub.topic('command'), payload=b'turn_off', retain=True))
        self.assertFalse(calls)
        pub._on_message(None, None, types.SimpleNamespace(
            topic=pub.topic('command'), payload=b'turn_off', retain=False))
        self.assertEqual(calls[0][1], 'turn_off')
        pub._on_message(None, None, types.SimpleNamespace(topic='homeassistant/status'))
        self.assertFalse(pub._announced)

    def test_only_supported_commands(self):
        self.assertEqual(ha_local.command_method('screensaver', 'OFF'), 'screensaver.off')
        self.assertEqual(ha_local.command_method('media', 'turn_off'), 'game.kill')
        self.assertIsNone(ha_local.command_method('media', 'reboot'))

    def test_lan_publisher_works_with_cloud_disabled_and_cleans_up(self):
        async def check():
            cfg = {'homeassistant': {'enabled': False, 'cabinet_enabled': True,
                                     'mqtt_host': '192.168.1.20'}}
            created = []
            def factory(config, *args):
                p = Mock(); p.enabled = config['homeassistant']['enabled']
                p.client = None; created.append(p); return p
            async def end(_):
                raise asyncio.CancelledError()
            with patch.object(ha_local.integrations, 'HomeAssistantPublisher', factory), \
                 patch.object(ha_local.asyncio, 'sleep', end):
                with self.assertRaises(asyncio.CancelledError):
                    await ha_local.run(lambda: cfg, lambda: {}, lambda: {}, Mock(), Mock())
            created[0].connect.assert_called_once()
            created[0].publish.assert_called_once()
            created[0].disconnect.assert_called_once()
            self.assertFalse(cfg['homeassistant']['enabled'])
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
