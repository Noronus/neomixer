import unittest
from unittest import mock

import pulsectl

from neomixer.scripts import pmctl
from neomixer.model.app_model import AppModel
from neomixer.model.config_model import ConfigModel
from neomixer.model.device_model import DeviceModel
from neomixer.model.known_app import KnownApp
from neomixer.repository.device_repository import DeviceRepository
from neomixer.controller.app_registry import AppRegistry, SETTLE_SECONDS


class _FakePersistence:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


def _stream(index=1, key='firefox', device='default_sink', volume=100, mute=False, pinned=False, app_type='sink_input', **kwargs):
    return AppModel(app_type=app_type, index=index, label=key.title(), icon=None, volume=volume, mute=mute,
                    device=device, key=key, object_id=index + 100, pinned=pinned, **kwargs)


def _sink(name):
    return DeviceModel(name=name, device_type='sink', device_class='virtual', primary=False,
                       channels=2, channel_list=['FL', 'FR'], selected_channels=[True, True])


class RegistryTestCase(unittest.TestCase):

    def setUp(self):
        self.now = 1000.0
        self.pins = {}
        self.existing = {'default_sink', 'media', 'chat'}

        patches = {
            'pin_app': mock.patch.object(pmctl, 'pin_app', return_value=True),
            'unpin_app': mock.patch.object(pmctl, 'unpin_app', return_value=True),
            'app_volume': mock.patch.object(pmctl, 'app_volume', return_value=True),
            'app_mute': mock.patch.object(pmctl, 'app_mute', return_value=True),
            'get_app_pins': mock.patch.object(pmctl, 'get_app_pins', side_effect=lambda: self.pins),
            'get_device_by_name': mock.patch.object(
                pmctl, 'get_device_by_name', side_effect=lambda _type, name: object() if name in self.existing else None
            ),
        }
        for name, patch in patches.items():
            setattr(self, name, patch.start())
        self.addCleanup(mock.patch.stopall)

        self.config = ConfigModel()
        self.config.devices['vi']['1'] = _sink('media')
        self.config.devices['vi']['2'] = _sink('chat')
        self.registry = self._new_registry()
        self.events = []
        self.registry.connect('entry_new', lambda entry: self.events.append(('new', entry.key)))
        self.registry.connect('entry_change', lambda entry: self.events.append(('change', entry.key)))
        self.registry.connect('entry_remove', lambda _type, key: self.events.append(('remove', key)))

    def _new_registry(self):
        return AppRegistry(DeviceRepository(_FakePersistence(self.config)), clock=lambda: self.now)

    def entry(self, key='firefox', app_type='sink_input'):
        return self.registry.entries[app_type][key]


class StreamLifecycleTest(RegistryTestCase):

    def test_a_program_stays_listed_after_its_stream_ends(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.assertEqual(self.entry().state, 'active')
        self.registry.stream_remove('sink_input', 1)
        self.assertEqual(self.entry().state, 'offline')
        self.assertIn(('new', 'firefox'), self.events)
        self.assertNotIn(('remove', 'firefox'), self.events)

    def test_it_is_still_listed_after_a_restart_of_neomixer(self):
        self.registry.stream_new('sink_input', 1, _stream())
        registry = self._new_registry()
        self.assertEqual(registry.entries['sink_input']['firefox'].state, 'offline')

    def test_streams_of_a_program_share_one_entry(self):
        self.registry.stream_new('sink_input', 1, _stream(1))
        self.registry.stream_new('sink_input', 2, _stream(2))
        self.assertEqual(len(self.registry.entries['sink_input']), 1)
        self.assertEqual(set(self.entry().streams), {1, 2})
        self.registry.stream_remove('sink_input', 1)
        self.assertEqual(self.entry().state, 'active')

    def test_playback_and_recording_are_separate_entries(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.registry.stream_new('source_output', 1, _stream(app_type='source_output'))
        self.assertIn('firefox', self.registry.entries['source_output'])
        self.registry.set_device('source_output', 'firefox', 'media.monitor')
        self.assertIsNone(self.entry().known.device)

    def test_unknown_stream_remove_is_ignored(self):
        self.registry.stream_remove('sink_input', 99)
        self.assertEqual(self.events, [])

    def test_change_before_new_creates_the_stream(self):
        self.registry.stream_change('sink_input', 1, _stream())
        self.assertEqual(self.entry().state, 'active')


class ClientTest(RegistryTestCase):

    PROPS = {'application.name': 'Firefox', 'application.process.binary': 'firefox', 'application.process.id': '1'}

    def test_connected_program_is_listed_before_it_plays(self):
        self.registry.client_new(5, self.PROPS)
        self.assertEqual(self.entry().state, 'idle')
        self.assertEqual(self.events, [('new', 'firefox')])

    def test_program_stays_listed_after_disconnecting(self):
        self.registry.client_new(5, self.PROPS)
        self.registry.client_remove(5)
        self.assertEqual(self.entry().state, 'offline')

    def test_playing_wins_over_connected(self):
        self.registry.client_new(5, self.PROPS)
        self.registry.stream_new('sink_input', 1, _stream())
        self.assertEqual(self.entry().state, 'active')
        self.registry.stream_remove('sink_input', 1)
        self.assertEqual(self.entry().state, 'idle')

    def test_system_clients_are_not_listed(self):
        self.registry.client_new(5, {'application.name': 'WirePlumber', 'application.process.binary': 'wireplumber'})
        self.assertEqual(self.registry.entries['sink_input'], {})

    def test_own_connections_are_not_listed(self):
        with mock.patch('os.getpid', return_value=42):
            self.registry.client_new(5, {**self.PROPS, 'application.process.id': '42'})
        self.assertEqual(self.registry.entries['sink_input'], {})


class RuleTest(RegistryTestCase):

    def remember(self, **kwargs):
        self.config.apps['sink_input']['firefox'] = KnownApp(key='firefox', label='Firefox', **kwargs)
        self.registry = self._new_registry()

    def test_choosing_a_device_pins_every_stream(self):
        self.registry.stream_new('sink_input', 1, _stream(1))
        self.registry.stream_new('sink_input', 2, _stream(2))
        self.registry.set_device('sink_input', 'firefox', 'media')
        self.assertEqual(self.pin_app.call_args_list, [
            mock.call(101, 'sink', 'media'), mock.call(102, 'sink', 'media')
        ])
        self.assertEqual(self.entry().known.device, 'media')

    def test_selector_shows_the_choice_while_the_stream_is_still_moving(self):
        self.pins = {101: True}
        self.registry.stream_new('sink_input', 1, _stream(device='chat'))
        self.assertEqual(self.entry().device, 'chat')
        self.registry.set_device('sink_input', 'firefox', 'media')
        self.assertEqual(self.entry().device, 'media')

    def test_choice_applies_to_the_stream_of_a_restarted_program(self):
        self.remember(device='media')
        self.registry.stream_new('sink_input', 1, _stream())
        self.pin_app.assert_called_once_with(101, 'sink', 'media')

    def test_program_can_be_set_up_while_offline(self):
        self.registry.client_new(5, ClientTest.PROPS)
        self.registry.client_remove(5)
        self.registry.set_device('sink_input', 'firefox', 'chat')
        self.pin_app.assert_not_called()
        self.registry.stream_new('sink_input', 1, _stream())
        self.pin_app.assert_called_once_with(101, 'sink', 'chat')

    def test_stream_already_in_place_gets_pinned_but_not_moved(self):
        self.remember(device='media')
        self.registry.stream_new('sink_input', 1, _stream(device='media', pinned=False))
        self.pin_app.assert_called_once()
        self.pin_app.reset_mock()
        self.registry.stream_change('sink_input', 1, _stream(device='media'))
        self.pin_app.assert_not_called()

    def test_following_the_default_is_a_choice_too(self):
        self.remember(device='')
        self.pins = {101: True}
        self.registry.stream_new('sink_input', 1, _stream(device='media'))
        self.unpin_app.assert_called_once_with(101)

    def test_no_choice_leaves_the_stream_alone(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.pin_app.assert_not_called()
        self.unpin_app.assert_not_called()
        self.app_volume.assert_not_called()

    def test_missing_device_is_applied_once_it_appears(self):
        self.remember(device='media')
        self.existing.discard('media')
        self.registry.stream_new('sink_input', 1, _stream())
        self.pin_app.assert_not_called()

        self.existing.add('media')
        self.registry.device_appeared()
        self.pin_app.assert_called_once_with(101, 'sink', 'media')

    def test_stream_that_refuses_to_move_is_left_alone(self):
        self.remember(device='media')
        self.registry.stream_new('sink_input', 1, _stream(movable=False))
        self.pin_app.assert_not_called()

    def test_volume_and_mute_are_restored(self):
        self.remember(volume=40, mute=True)
        self.registry.stream_new('sink_input', 1, _stream())
        self.app_volume.assert_called_once_with('sink_input', 1, 40)
        self.app_mute.assert_called_once_with('sink_input', 1, True)

    def test_volume_applies_to_every_stream(self):
        self.registry.stream_new('sink_input', 1, _stream(1))
        self.registry.stream_new('sink_input', 2, _stream(2))
        self.registry.set_volume('sink_input', 'firefox', 55)
        self.assertEqual(self.app_volume.call_count, 2)
        self.assertEqual(self.entry().known.volume, 55)
        self.assertEqual(self.entry().volume, 55)

    def test_vanished_stream_does_not_break_the_others(self):
        self.app_volume.side_effect = [pulsectl.PulseIndexError(), True]
        self.registry.stream_new('sink_input', 1, _stream(1))
        self.registry.stream_new('sink_input', 2, _stream(2))
        self.registry.set_volume('sink_input', 'firefox', 55)
        self.assertEqual(self.app_volume.call_count, 2)

    def test_session_manager_placing_a_new_stream_is_corrected(self):
        self.remember(device='media')
        self.registry.stream_new('sink_input', 1, _stream())
        self.pin_app.reset_mock()

        # it lands somewhere else right after starting
        self.pins = {101: True}
        self.now += SETTLE_SECONDS / 2
        self.registry.stream_change('sink_input', 1, _stream(device='chat'))
        self.pin_app.assert_called_once_with(101, 'sink', 'media')
        self.assertEqual(self.entry().known.device, 'media')


class ExternalChangesTest(RegistryTestCase):

    def settled_stream(self, **kwargs):
        self.config.apps['sink_input']['firefox'] = KnownApp(key='firefox', label='Firefox', **kwargs)
        self.registry = self._new_registry()
        self.pins = {101: True}
        self.registry.stream_new('sink_input', 1, _stream(device=kwargs.get('device') or 'default_sink'))
        self.now += SETTLE_SECONDS + 1
        self.pin_app.reset_mock()

    def test_move_by_another_tool_is_remembered(self):
        self.settled_stream(device='media')
        self.registry.stream_change('sink_input', 1, _stream(device='chat'))
        self.assertEqual(self.entry().known.device, 'chat')
        self.pin_app.assert_not_called()

    def test_fallback_after_the_device_went_away_is_not_a_choice(self):
        self.settled_stream(device='media')
        self.existing.discard('media')
        self.registry.stream_change('sink_input', 1, _stream(device='chat'))
        self.assertEqual(self.entry().known.device, 'media')

    def test_move_to_a_device_neomixer_does_not_manage_is_not_remembered(self):
        self.settled_stream(device='media')
        self.registry.stream_change('sink_input', 1, _stream(device='default_sink'))
        self.assertEqual(self.entry().known.device, 'media')

    def test_following_the_default_device_is_not_a_choice(self):
        self.settled_stream()
        self.pins = {}
        self.registry.stream_change('sink_input', 1, _stream(device='chat'))
        self.assertIsNone(self.entry().known.device)

    def test_volume_set_elsewhere_is_remembered(self):
        self.settled_stream()
        self.registry.stream_change('sink_input', 1, _stream(device='default_sink', volume=30, mute=True))
        self.assertEqual(self.entry().known.volume, 30)
        self.assertTrue(self.entry().known.mute)

    def test_volume_during_startup_is_not_remembered(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.registry.stream_change('sink_input', 1, _stream(volume=30))
        self.assertIsNone(self.entry().known.volume)


class ManagingTheListTest(RegistryTestCase):

    def test_forget_removes_an_offline_program_with_its_settings(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.registry.set_device('sink_input', 'firefox', 'media')
        self.registry.stream_remove('sink_input', 1)
        self.assertTrue(self.registry.forget('sink_input', 'firefox'))
        self.assertNotIn('firefox', self.config.apps['sink_input'])
        self.assertIn(('remove', 'firefox'), self.events)

    def test_running_program_cannot_be_forgotten(self):
        self.registry.stream_new('sink_input', 1, _stream())
        self.assertFalse(self.registry.forget('sink_input', 'firefox'))
        self.assertIn('firefox', self.registry.entries['sink_input'])

    def test_program_can_be_added_before_it_is_ever_seen(self):
        self.registry.add_manually('sink_input', 'spotify', 'Spotify', binary='spotify')
        self.assertEqual(self.entry('spotify').state, 'offline')
        self.registry.set_device('sink_input', 'spotify', 'media')
        self.registry.stream_new('sink_input', 1, _stream(key='spotify'))
        self.pin_app.assert_called_once_with(101, 'sink', 'media')


class BootstrapTest(RegistryTestCase):

    def test_running_streams_go_where_they_belong_at_startup(self):
        self.config.apps['sink_input']['firefox'] = KnownApp(key='firefox', label='Firefox', device='media')
        self.registry = self._new_registry()

        class _Volume:
            values = [1.0, 1.0]

        class _PaStream:
            index = 7
            mute = 0
            device_name = 'default_sink'
            volume = _Volume()
            client_props = {}
            proplist = {'application.name': 'Firefox', 'application.process.binary': 'firefox', 'object.id': '50'}

        client = mock.Mock(index=3, proplist=ClientTest.PROPS)
        with mock.patch.object(pmctl, 'list_apps', side_effect=lambda t: [_PaStream()] if t == 'sink_input' else []), \
                mock.patch.object(pmctl, 'list_clients', return_value=[client]):
            self.registry.bootstrap()

        self.pin_app.assert_called_once_with(50, 'sink', 'media')
        self.assertEqual(self.entry().state, 'active')
        self.assertEqual(self.entry().clients, {3})

    def test_remembered_programs_are_announced_at_startup(self):
        self.config.apps['sink_input']['firefox'] = KnownApp(key='firefox', label='Firefox', device='media')
        self.registry = self._new_registry()
        seen = []
        self.registry.connect('entry_new', lambda entry: seen.append((entry.key, entry.state)))
        self.registry.connect('entry_change', lambda entry: seen.append(('change', entry.key)))
        with mock.patch.object(pmctl, 'list_apps', return_value=[]), mock.patch.object(pmctl, 'list_clients', return_value=[]):
            self.registry.bootstrap()
            self.assertEqual(seen, [('firefox', 'offline')])
            self.registry.bootstrap()
        self.assertEqual(seen[1:], [('change', 'firefox')])

    def test_bootstrap_forgets_streams_of_a_dead_server(self):
        self.registry.stream_new('sink_input', 1, _stream())
        with mock.patch.object(pmctl, 'list_apps', return_value=[]), mock.patch.object(pmctl, 'list_clients', return_value=[]):
            self.registry.bootstrap()
        self.assertEqual(self.entry().state, 'offline')
