import json
from types import SimpleNamespace
from unittest import mock

import pytest

from neomixer.controller import remote_controller
from neomixer.controller.remote_controller import Client, RemoteController, answer_to
from neomixer.model.config_model import ConfigModel
from neomixer.model.device_model import DeviceModel
from neomixer.model.remote_model import RemotePhone
from neomixer.repository.device_repository import DeviceRepository
from neomixer.scripts import pw_phone

SECRET = 'ab' * 32


class _FakePersistence:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


class _FakeSocket:
    '''Takes whatever is sent, as a phone that reads everything at once.'''

    def __init__(self):
        self.sent = b''
        self.closed = False

    def send(self, data):
        self.sent += data
        return len(data)

    def close(self):
        self.closed = True

    def messages(self):
        lines, self.sent = self.sent.splitlines(), b''
        return [json.loads(line) for line in lines]


class _FakeMixer:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def _device(name, device_type='sink', device_class='virtual', **kwargs):
    return DeviceModel(name=name, device_type=device_type, device_class=device_class, channels=2,
                       channel_list=['FL', 'FR'], selected_channels=[True, True], **kwargs)


def _entry(key, label, state='active', volume=80, mute=False):
    return SimpleNamespace(key=key, label=label, state=state, volume=volume, mute=mute)


@pytest.fixture
def config():
    return ConfigModel(
        devices={
            'vi': {'1': _device('Main'), '3': _device('Chat')},
            'hi': {'1': _device('mic', 'source', 'hardware')},
            'a': {'2': _device('speakers', device_class='hardware'), '10': _device(pw_phone.SINK_NAME, device_class='hardware')},
            'b': {'1': _device('Stream', 'source')},
        },
        remote_phones={'known': RemotePhone(name='Known', secret=SECRET)},
    )


@pytest.fixture
def remote(config):
    registry = SimpleNamespace(entries={
        'sink_input': {'firefox': _entry('firefox', 'Firefox'), 'mpv': _entry('mpv', 'mpv', state='offline')},
        'source_output': {'obs': _entry('obs', 'OBS')},
    })
    controller = RemoteController(DeviceRepository(_FakePersistence(config)), registry, _FakeMixer())
    with mock.patch.object(remote_controller.GLib, 'timeout_add', return_value=1), \
            mock.patch.object(remote_controller.GLib, 'source_remove'), \
            mock.patch.object(remote_controller.pw_phone, 'stop'):
        yield controller


def _connect(remote, address='10.0.0.7'):
    client = Client(_FakeSocket(), address)
    remote.clients.append(client)
    return client


def _say(remote, client, **message):
    remote.handle_line(client, json.dumps(message).encode())
    return client.sock.messages() if client.sock is not None else []


def _welcomed(remote, phone_id='known'):
    client = _connect(remote)
    nonce = _say(remote, client, t='hello', id=phone_id, name='Pixel')[0]['nonce']
    _say(remote, client, t='auth', mac=answer_to(SECRET, nonce))
    assert client.authed
    return client


# Getting in

def test_a_known_phone_gets_in_with_the_right_answer(remote):
    client = _connect(remote)
    challenge = _say(remote, client, t='hello', id='known', name='Pixel')
    assert [message['t'] for message in challenge] == ['challenge']

    answers = _say(remote, client, t='auth', mac=answer_to(SECRET, challenge[0]['nonce']))
    assert [message['t'] for message in answers] == ['welcome', 'state']
    assert client.authed
    assert remote.connected_ids() == {'known'}


def test_a_known_phone_with_the_wrong_answer_is_dropped(remote):
    client = _connect(remote)
    sock = client.sock
    _say(remote, client, t='hello', id='known', name='Pixel')
    remote.handle_line(client, json.dumps({'t': 'auth', 'mac': answer_to('cd' * 32, client.nonce)}).encode())

    assert [message['t'] for message in sock.messages()] == ['denied']
    assert sock.closed and client not in remote.clients


def test_an_answer_cannot_be_tried_twice(remote):
    '''A second auth has no challenge to answer: the nonce is used up.'''
    client = _connect(remote)
    nonce = _say(remote, client, t='hello', id='known', name='Pixel')[0]['nonce']
    client.nonce = None
    assert _say(remote, client, t='auth', mac=answer_to(SECRET, nonce)) == []
    assert not client.authed


def test_nothing_is_done_for_a_phone_that_is_not_in(remote):
    client = _connect(remote)
    _say(remote, client, t='hello', id='known', name='Pixel')
    assert _say(remote, client, t='volume', type='vi', id='1', value=10) == []
    assert remote.mixer.calls == []


def test_a_new_phone_is_asked_about_and_remembered_when_allowed(remote, config):
    asked = []
    remote.connect('pair_request', lambda name, address, answer: asked.append((name, address, answer)))
    client = _connect(remote)

    assert [message['t'] for message in _say(remote, client, t='hello', id='new', name='Tablet')] == ['pair']
    assert _say(remote, client, t='pair', secret=SECRET) == []
    assert asked[0][:2] == ('Tablet', '10.0.0.7')
    assert not client.authed

    asked[0][2](True)
    assert [message['t'] for message in client.sock.messages()] == ['welcome', 'state']
    assert config.remote_phones['new'].secret == SECRET


def test_a_new_phone_that_is_denied_is_dropped_and_not_remembered(remote, config):
    asked = []
    remote.connect('pair_request', lambda name, address, answer: asked.append(answer))
    client = _connect(remote)
    sock = client.sock
    _say(remote, client, t='hello', id='new', name='Tablet')
    _say(remote, client, t='pair', secret=SECRET)

    asked[0](False)
    assert [message['t'] for message in sock.messages()] == ['denied']
    assert 'new' not in config.remote_phones and client not in remote.clients


def test_a_phone_that_left_while_being_asked_about_is_not_remembered(remote, config):
    asked = []
    remote.connect('pair_request', lambda name, address, answer: asked.append(answer))
    client = _connect(remote)
    _say(remote, client, t='hello', id='new', name='Tablet')
    _say(remote, client, t='pair', secret=SECRET)
    remote._drop(client)

    asked[0](True)
    assert 'new' not in config.remote_phones


def test_a_secret_too_short_to_be_one_is_refused(remote):
    asked = []
    remote.connect('pair_request', lambda *args: asked.append(args))
    client = _connect(remote)
    sock = client.sock
    _say(remote, client, t='hello', id='new', name='Tablet')
    remote.handle_line(client, json.dumps({'t': 'pair', 'secret': 'abcd'}).encode())

    assert asked == [] and [message['t'] for message in sock.messages()] == ['denied']


def test_a_known_phone_cannot_pair_again_to_replace_its_secret(remote, config):
    client = _connect(remote)
    _say(remote, client, t='hello', id='known', name='Pixel')
    remote.handle_line(client, json.dumps({'t': 'pair', 'secret': 'cd' * 32}).encode())
    assert config.remote_phones['known'].secret == SECRET and not client.authed


def test_a_forgotten_phone_is_dropped_and_has_to_ask_again(remote, config):
    client = _welcomed(remote)
    sock = client.sock
    remote.forget('known')

    assert 'known' not in config.remote_phones and sock.closed
    again = _connect(remote)
    assert [message['t'] for message in _say(remote, again, t='hello', id='known', name='Pixel')] == ['pair']


def test_garbage_is_ignored(remote):
    client = _welcomed(remote)
    for line in (b'', b'not json', b'[1, 2]', b'{"no": "kind"}', b'{"t": "volume"}', b'{"t": "volume", "type": "vi", "id": "1", "value": "loud"}'):
        remote.handle_line(client, line)
    assert remote.mixer.calls == [] and client in remote.clients


# The state

def test_the_state_has_every_strip_with_its_routes_and_tags(remote, config):
    config.devices['vi']['1'].connections['a']['10'].state = True
    config.devices['vi']['1'].connections['b']['1'].state = True
    config.devices['vi']['3'].mute = True
    config.devices['a']['2'].present = False

    strips = {(strip['type'], strip['id']): strip for strip in remote.snapshot()['strips']}
    assert list(strips) == [('hi', '1'), ('vi', '1'), ('vi', '3'), ('a', '2'), ('a', '10'), ('b', '1')]
    assert strips[('vi', '1')]['routes'] == ['a10', 'b1']
    assert strips[('vi', '3')]['mute'] is True and strips[('vi', '3')]['routes'] == []
    # numbered by position, as on the mixer's strips: the ids have gaps
    assert [strips[key]['tag'] for key in (('a', '2'), ('a', '10'), ('b', '1'))] == ['A1', 'A2', 'B1']
    assert strips[('a', '2')]['present'] is False
    assert strips[('a', '10')].get('phone') is True and 'phone' not in strips[('a', '2')]


def test_the_state_has_the_programs(remote):
    apps = remote.snapshot()['apps']
    assert [(app['app_type'], app['key'], app['state']) for app in apps] == [
        ('sink_input', 'firefox', 'active'), ('sink_input', 'mpv', 'offline'), ('source_output', 'obs', 'active'),
    ]


def test_the_state_is_only_sent_when_it_changed(remote, config):
    client = _welcomed(remote)
    client.sock.messages()

    remote._send_states()
    assert client.sock.messages() == []

    config.devices['vi']['1'].volume = [55, 55]
    remote._send_states()
    sent = client.sock.messages()
    assert len(sent) == 1 and sent[0]['strips'][1]['volume'] == 55


# What a phone asks for

def test_commands_reach_the_mixer(remote):
    client = _welcomed(remote)
    _say(remote, client, t='volume', type='vi', id='1', value=70)
    _say(remote, client, t='mute', type='a', id='2', value=True)
    _say(remote, client, t='route', in_type='vi', in_id='1', out_type='b', out_id='1', value=True)
    _say(remote, client, t='app_volume', app_type='sink_input', key='firefox', value=30)
    _say(remote, client, t='app_mute', app_type='sink_input', key='firefox', value=True)

    assert remote.mixer.calls == [
        ('apply_volume', 'vi', '1', 70),
        ('apply_mute', 'a', '2', True),
        ('apply_route', 'vi', '1', 'b', '1', True),
        ('apply_app_volume', 'sink_input', 'firefox', 30),
        ('apply_app_mute', 'sink_input', 'firefox', True),
    ]


def test_a_volume_is_kept_within_what_a_fader_can_do(remote):
    client = _welcomed(remote)
    _say(remote, client, t='volume', type='vi', id='1', value=9000)
    _say(remote, client, t='volume', type='vi', id='1', value=-5)
    assert [call[3] for call in remote.mixer.calls] == [153, 0]


def test_a_route_between_the_wrong_kinds_of_devices_is_ignored(remote):
    client = _welcomed(remote)
    _say(remote, client, t='route', in_type='a', in_id='2', out_type='vi', out_id='1', value=True)
    assert remote.mixer.calls == []


def test_a_command_for_a_device_that_is_gone_does_not_drop_the_phone(remote):
    remote.mixer = SimpleNamespace(apply_volume=mock.Mock(side_effect=KeyError('9')))
    client = _welcomed(remote)
    _say(remote, client, t='volume', type='vi', id='9', value=70)
    assert client in remote.clients


def test_a_ping_is_answered(remote):
    client = _welcomed(remote)
    client.sock.messages()
    assert _say(remote, client, t='ping') == [{'t': 'pong'}]


# Audio

@pytest.fixture
def started(remote):
    '''Phone audio whose process starts and whose devices appear.'''
    process = mock.Mock()
    process.poll.return_value = None
    with mock.patch.object(remote_controller.pw_phone, 'start', return_value=process) as start, \
            mock.patch.object(remote_controller.pmctl, 'device_exists', return_value=True):
        yield start


def test_audio_plays_to_the_phone_that_asked(remote, started):
    client = _welcomed(remote)
    client.sock.messages()
    _say(remote, client, t='audio', on=True, port=40000)
    started.assert_called_once_with('10.0.0.7', 40000, remote.mic_port, 'Pixel')

    remote._check_audio_started(remote.audio_process, 3)
    answer = client.sock.messages()[-1]
    assert answer['t'] == 'audio' and answer['on'] is True and answer['mic_port'] == 47801


def test_the_phone_gets_its_strips_the_first_time_only(remote, config, started):
    del config.devices['a']['10']
    created = []
    remote.connect('device_new', created.append)
    client = _welcomed(remote)
    _say(remote, client, t='audio', on=True, port=40000)
    remote._check_audio_started(remote.audio_process, 3)

    assert [(schema['name'], schema['device_type'], schema['device_class'], schema['channels']) for schema in created] == [
        (pw_phone.SINK_NAME, 'sink', 'hardware', 2), (pw_phone.SOURCE_NAME, 'source', 'hardware', 1),
    ]
    # what the mixer makes of them has to be a valid device
    for schema in created:
        assert DeviceModel(**schema).get_type() in ('a', 'hi')

    config.devices['a']['1'] = DeviceModel(**created[0])
    config.devices['hi']['2'] = DeviceModel(**created[1])
    created.clear()
    _say(remote, client, t='audio', on=True, port=40000)
    remote._check_audio_started(remote.audio_process, 3)
    assert created == []


def test_audio_that_does_not_start_is_reported(remote):
    process = mock.Mock()
    process.poll.return_value = 1
    client = _welcomed(remote)
    client.sock.messages()
    with mock.patch.object(remote_controller.pw_phone, 'start', return_value=process), \
            mock.patch.object(remote_controller.pw_phone, 'last_error', return_value='no such module'):
        _say(remote, client, t='audio', on=True, port=40000)
        remote._check_audio_started(process, 3)

    answer = client.sock.messages()[-1]
    assert answer == {'t': 'audio', 'on': False, 'error': 'no such module'}
    assert remote.audio_client is None and remote.audio_process is None


def test_audio_stops_when_its_phone_goes(remote, started):
    client = _welcomed(remote)
    _say(remote, client, t='audio', on=True, port=40000)
    process = remote.audio_process

    remote._drop(client)
    remote_controller.pw_phone.stop.assert_called_with(process)
    assert remote.audio_client is None


def test_another_phone_takes_the_audio_over(remote, config, started):
    config.remote_phones['other'] = RemotePhone(name='Other', secret=SECRET)
    first = _welcomed(remote)
    second = _welcomed(remote, 'other')
    _say(remote, first, t='audio', on=True, port=40000)
    first.sock.messages()

    _say(remote, second, t='audio', on=True, port=40001)
    assert first.sock.messages() == [{'t': 'audio', 'on': False, 'error': 'taken'}]
    assert remote.audio_client is second


def test_a_phone_cannot_stop_the_audio_of_another(remote, config, started):
    config.remote_phones['other'] = RemotePhone(name='Other', secret=SECRET)
    first = _welcomed(remote)
    second = _welcomed(remote, 'other')
    _say(remote, first, t='audio', on=True, port=40000)

    _say(remote, second, t='audio', on=False)
    assert remote.audio_client is first


def test_a_port_that_is_none_is_not_played_to(remote, started):
    client = _welcomed(remote)
    _say(remote, client, t='audio', on=True, port=80)
    started.assert_not_called()
