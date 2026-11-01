import unittest
from unittest import mock

from neomixer.scripts import pmctl, pw_eq
from neomixer.model.signal_model import SignalModel
from neomixer.model.device_model import DeviceModel
from neomixer.model.config_model import ConfigModel
from neomixer.model.connection_model import ConnectionModel
from neomixer.repository.device_repository import DeviceRepository
from neomixer.controller.device_controller import DeviceController, RunningEq


class _FakePersistence:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


def _device(name, device_type='sink', device_class='virtual', **kwargs):
    return DeviceModel(name=name, device_type=device_type, device_class=device_class, primary=False,
                       channels=2, channel_list=['FL', 'FR'], selected_channels=[True, True], **kwargs)


class EqControllerTest(unittest.TestCase):

    def setUp(self):
        self.ports = {
            ('output', 'music'): ['monitor_FL', 'monitor_FR'],
            ('input', 'music'): ['playback_FL', 'playback_FR'],
            ('input', 'headset'): ['playback_FL', 'playback_FR'],
            ('output', 'output.nm_eq_music'): ['output_FL', 'output_FR'],
            ('input', 'input.nm_eq_music'): ['input_FL', 'input_FR'],
            ('output', 'output.nm_eq_headset'): ['output_FL', 'output_FR'],
            ('input', 'input.nm_eq_headset'): ['playback_FL', 'playback_FR'],
            ('input', 'bus'): ['input_MONO'],
            ('output', 'bus'): ['capture_MONO'],
            ('output', 'output.nm_eq_bus'): ['output_MONO'],
            ('input', 'input.nm_eq_bus'): ['input_MONO'],
        }
        patches = [
            mock.patch.object(pmctl, 'get_ports', side_effect=lambda t, n: self.ports.get((t, n), [])),
            mock.patch.object(pmctl, 'link', return_value=True),
            mock.patch.object(pmctl, '_loopback_streams', return_value=([], [])),
            mock.patch.object(pw_eq, 'start', return_value=mock.Mock(pid=1)),
            mock.patch.object(pw_eq, 'stop'),
            mock.patch.object(pw_eq, 'wait_ready', return_value=True),
            mock.patch.object(pw_eq, 'set_params', return_value=True),
            mock.patch.object(pw_eq, 'running_signature', return_value=None),
            mock.patch.object(pw_eq, 'wait_linked', return_value=True),
        ]
        (self.get_ports, self.link, _, self.start, self.stop, self.wait_ready, self.set_params,
         self.running_signature, self.wait_linked) = [patch.start() for patch in patches]
        self.addCleanup(mock.patch.stopall)

        config = ConfigModel()
        self.music = _device('music')
        self.headset = _device('headset', device_class='hardware')
        self.music.connections['a']['1'] = ConnectionModel(
            nick='headset', state=True, input_sel_channels=[True, True], output_sel_channels=[True, True]
        )
        self.bus = DeviceModel(name='bus', device_type='source', device_class='virtual', primary=False, channels=1,
                               channel_list=['MONO'], selected_channels=[True])
        self.music.connections['b']['1'] = ConnectionModel(
            nick='bus', state=False, input_sel_channels=[True, True], output_sel_channels=[True]
        )
        config.devices['vi']['1'] = self.music
        config.devices['a']['1'] = self.headset
        config.devices['b']['1'] = self.bus

        self.controller = DeviceController.__new__(DeviceController)
        SignalModel.__init__(self.controller)
        self.controller._running_eqs = {}
        self.controller._loopback_procs = {}
        self.controller.device_repository = DeviceRepository(_FakePersistence(config))

    def links(self):
        return [(call.args[0], call.args[1], call.kwargs.get('state', True)) for call in self.link.call_args_list]

    def test_routes_play_from_the_device_without_eq(self):
        self.controller.set_connection('vi', '1', 'a', '1', True, soft=True)
        self.assertIn(('music:monitor_FL', 'headset:playback_FL', True), self.links())

    def test_enabling_puts_the_eq_between_the_device_and_its_routes(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')

        self.start.assert_called_once()
        links = self.links()
        self.assertIn(('music:monitor_FL', 'input.nm_eq_music:input_FL', True), links)
        self.assertIn(('music:monitor_FR', 'input.nm_eq_music:input_FR', True), links)
        self.assertIn(('music:monitor_FL', 'headset:playback_FL', False), links)
        self.assertEqual(links[-2:], [
            ('output.nm_eq_music:output_FL', 'headset:playback_FL', True),
            ('output.nm_eq_music:output_FR', 'headset:playback_FR', True),
        ])

    def test_disabled_eq_does_not_start_anything(self):
        self.controller.set_eq('vi', '1')
        self.start.assert_not_called()
        self.link.assert_not_called()

    def test_changing_a_running_eq_does_not_touch_the_routes(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.link.reset_mock()
        self.start.reset_mock()

        self.music.eq.bands[3].gain = 6
        self.controller.set_eq('vi', '1')
        self.set_params.assert_called_once_with('music', self.music.eq)
        self.start.assert_not_called()
        self.link.assert_not_called()

    def test_switching_off_keeps_the_filters_running_for_instant_comparing(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.link.reset_mock()
        self.stop.reset_mock()

        self.music.eq.enabled = False
        self.controller.set_eq('vi', '1')
        self.set_params.assert_called_once()
        self.stop.assert_not_called()
        self.link.assert_not_called()

    def test_new_filter_type_rebuilds_the_eq_and_relinks(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.start.reset_mock()
        self.link.reset_mock()

        self.music.eq.bands[0].type = 'highpass'
        self.controller.set_eq('vi', '1')
        self.start.assert_called_once()
        links = self.links()
        self.assertIn(('output.nm_eq_music:output_FL', 'headset:playback_FL', False), links)
        self.assertIn(('output.nm_eq_music:output_FL', 'headset:playback_FL', True), links)

    def test_setup_takes_over_a_matching_eq_from_an_earlier_run(self):
        self.music.eq.enabled = True
        self.running_signature.return_value = pw_eq.signature(['FL', 'FR'], self.music.eq)
        self.controller._setup_eq(self.music)
        self.start.assert_not_called()
        self.stop.assert_not_called()
        self.set_params.assert_called_once()
        self.assertIsNone(self.controller._running_eqs['music'].process)

    def test_setup_replaces_an_eq_that_no_longer_matches(self):
        self.music.eq.enabled = True
        self.running_signature.return_value = 'old'
        self.controller._setup_eq(self.music)
        self.stop.assert_called_once_with('music')
        self.start.assert_called_once()

    def test_setup_replaces_an_eq_whose_nodes_died_with_the_server(self):
        self.music.eq.enabled = True
        self.running_signature.return_value = pw_eq.signature(['FL', 'FR'], self.music.eq)
        self.wait_ready.side_effect = [False, True]
        self.controller._setup_eq(self.music)
        self.start.assert_called_once()

    def test_setup_stops_a_leftover_eq_that_is_switched_off(self):
        self.controller._setup_eq(self.music)
        self.stop.assert_called_once_with('music')
        self.start.assert_not_called()

    def test_device_without_ports_gets_its_eq_later(self):
        self.ports[('output', 'music')] = []
        self.music.eq.enabled = True
        self.controller._setup_eq(self.music)
        self.start.assert_not_called()
        self.assertNotIn('music', self.controller._running_eqs)

    def test_failed_start_leaves_the_direct_route_alone(self):
        self.music.eq.enabled = True
        self.wait_ready.return_value = False
        self.controller.set_eq('vi', '1')
        self.assertNotIn('music', self.controller._running_eqs)
        self.link.assert_not_called()

    def test_hardware_output_gets_a_smart_filter(self):
        self.headset.eq.enabled = True
        self.controller.set_eq('a', '1')
        self.start.assert_called_once_with('headset', ['FL', 'FR'], self.headset.eq, True)

        # the routes play into it, and the session manager links it to the device
        links = self.links()
        self.assertIn(('music:monitor_FL', 'headset:playback_FL', False), links)
        self.assertIn(('music:monitor_FL', 'input.nm_eq_headset:playback_FL', True), links)
        self.assertNotIn(('output.nm_eq_headset:output_FL', 'headset:playback_FL', True), links)

    def test_smart_filter_is_linked_by_hand_if_the_session_manager_does_not(self):
        self.wait_linked.return_value = False
        self.headset.eq.enabled = True
        self.controller.set_eq('a', '1')
        self.assertIn(('output.nm_eq_headset:output_FL', 'headset:playback_FL', True), self.links())

    def test_virtual_output_gets_its_eq_linked_by_hand(self):
        self.music.connections['b']['1'].state = True
        self.bus.eq.enabled = True
        self.controller.set_eq('b', '1')
        self.start.assert_called_once_with('bus', ['MONO'], self.bus.eq, False)
        self.wait_linked.assert_not_called()
        links = self.links()
        self.assertIn(('output.nm_eq_bus:output_MONO', 'bus:input_MONO', True), links)
        self.assertIn(('music:monitor_FL', 'input.nm_eq_bus:input_MONO', True), links)

    def test_eq_on_both_ends_of_a_route(self):
        self.music.eq.enabled = True
        self.headset.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.controller.set_eq('a', '1')
        self.assertEqual(self.links()[-2:], [
            ('output.nm_eq_music:output_FL', 'input.nm_eq_headset:playback_FL', True),
            ('output.nm_eq_music:output_FR', 'input.nm_eq_headset:playback_FR', True),
        ])

    def test_unplugged_output_loses_its_eq_and_gets_a_new_one_when_back(self):
        self.headset.eq.enabled = True
        self.controller.set_eq('a', '1')
        self.start.reset_mock()

        self.controller.handle_unplug('a', '1')
        self.assertNotIn('headset', self.controller._running_eqs)
        self.controller.handle_hotplug('a', '1')
        self.start.assert_called_once()
        self.assertIn('headset', self.controller._running_eqs)

    def test_input_keeps_its_eq_through_an_unplug(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.controller.handle_unplug('vi', '1')
        self.assertIn('music', self.controller._running_eqs)

    def test_eq_whose_nodes_are_gone_is_started_again(self):
        self.music.eq.enabled = True
        self.controller.set_eq('vi', '1')
        self.start.reset_mock()
        self.wait_ready.side_effect = [False, True]
        self.controller._setup_eq(self.music)
        self.start.assert_called_once()

    def test_failed_link_warning_names_the_channels_of_the_devices(self):
        self.headset.eq.enabled = True
        self.controller.set_eq('a', '1')
        self.link.side_effect = lambda source, target, state=True: 'playback_FR' not in target
        self.controller.set_connection('vi', '1', 'a', '1', True, soft=True)
        self.assertEqual(self.music.connections['a']['1'].failed_pairs, ['monitor_FR -> playback_FR'])

    def test_loopback_route_records_from_the_eq(self):
        self.controller._running_eqs['music'] = RunningEq(None, 'sig', ['FL', 'FR'])
        name = pmctl.make_loopback_name('music', 'headset')
        connection = self.music.connections['a']['1']
        connection.use_loopback = True
        self.ports[('input', f'input.{name}')] = ['input_AUX0', 'input_AUX1']
        self.ports[('output', f'output.{name}')] = ['output_AUX0', 'output_AUX1']
        with mock.patch.object(self.controller, '_loopback_is_current', return_value=True), \
                mock.patch.object(pmctl, 'set_route_volume', return_value=True), \
                mock.patch.object(pmctl, 'get_links', return_value=[]):
            self.controller._set_loopback_connection(name, self.music, self.headset, connection, True)

        self.assertIn(('output.nm_eq_music:output_FL', f'input.{name}:input_AUX0', True), self.links())
