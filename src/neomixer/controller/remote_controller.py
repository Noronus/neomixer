'''
Lets the Android app control the mixer over the local network, and plays through the phone.

A phone finds the mixer with a UDP broadcast and then talks to it over TCP, one JSON object
per line. A phone nobody allowed yet is asked about on this machine, once: after that it proves
itself on every connection with the secret it was paired with, which never crosses the network
again. Nothing is encrypted, so this is for a network one trusts, as a home network is.

    phone                                    mixer
    {"t":"hello","id","name"}           ->
                                        <-   {"t":"challenge","nonce"}       known phone
    {"t":"auth","mac"}                  ->
                                        <-   {"t":"pair"}                    new phone
    {"t":"pair","secret"}               ->   ... asks on screen ...
                                        <-   {"t":"welcome"} or {"t":"denied"}
                                        <-   {"t":"state",...}               whenever it changes
    {"t":"volume","type","id","value"}  ->   and "mute", "route", "app_volume", "app_mute"
    {"t":"audio","on","port"}           ->
                                        <-   {"t":"audio","on","mic_port",...}

The state goes out whole whenever it differs from what a phone last got. Comparing all of it is
what makes that reliable, as it is for saving the config: no change can forget to announce itself.

Everything runs on the main loop.
'''
import hmac
import json
import time
import socket
import hashlib
import logging
import secrets

from gi.repository import GLib

from neomixer.scripts import pmctl, pw_phone
from neomixer.settings import VERSION
from neomixer.model.remote_model import RemotePhone
from neomixer.model.signal_model import SignalModel

LOG = logging.getLogger('generic')

PROTOCOL = 1
DISCOVERY_QUESTION = b'NEOMIXER?'
STATE_INTERVAL_MS = 100
CHECK_INTERVAL_MS = 2000
# A phone says something every few seconds. One that left the network says nothing at all.
CLIENT_TIMEOUT_SECONDS = 10
MAX_LINE_BYTES = 64 * 1024
MAX_UNSENT_BYTES = 1024 * 1024
MAX_CLIENTS = 8
MAX_VOLUME = 153
AUDIO_START_CHECKS = 40
AUDIO_START_INTERVAL_MS = 100

DEVICE_ORDER = ('hi', 'vi', 'a', 'b')
INPUT_TYPES = ('hi', 'vi')
OUTPUT_TYPES = ('a', 'b')
APP_TYPES = ('sink_input', 'source_output')

# What the phone's two devices are in the mixer: (name, strip name, sound server type, channels, positions)
PHONE_DEVICES = (
    (pw_phone.SINK_NAME, 'Phone', 'sink', pw_phone.SINK_CHANNELS, ['FL', 'FR']),
    (pw_phone.SOURCE_NAME, 'Phone Mic', 'source', pw_phone.SOURCE_CHANNELS, ['MONO']),
)


def answer_to(secret: str, nonce: str) -> str:
    '''What a phone that has "secret" answers a challenge with.'''
    return hmac.new(bytes.fromhex(secret), bytes.fromhex(nonce), hashlib.sha256).hexdigest()


def local_address() -> str:
    '''The address this machine has on the network, as a phone would have to type it.'''
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Nothing is sent: connecting a UDP socket only picks the route
        probe.connect(('10.255.255.255', 1))
        return probe.getsockname()[0]
    except OSError:
        return ''
    finally:
        probe.close()


class Client:
    '''A phone's connection.'''

    def __init__(self, sock, address):
        self.sock = sock
        self.address = address
        self.phone_id = None
        self.name = ''
        self.nonce = None
        self.authed = False
        self.asking = False
        self.unread = b''
        self.unsent = b''
        self.sent_state = None
        self.last_heard = time.monotonic()
        self.watches = {}


class RemoteController(SignalModel):
    '''
    The server the Android app connects to.

    Signals:
        pair_request(name: str, address: str, answer: Callable[[bool], None]):
            A phone that was never allowed asks to be. "answer" takes the decision.
        device_new(schema: dict):
            A device of the phone's is missing in the mixer and should be created.
        changed():
            The list of paired or connected phones changed, or whether the server runs.
    '''

    def __init__(self, device_repository, app_registry, mixer, clock=time.monotonic):
        '''
            "mixer" does what a phone asks for, by way of the mixer's own controls: see the
                apply_ methods of GtkController
        '''
        super().__init__()
        self.device_repository = device_repository
        self.config = device_repository.config
        self.app_registry = app_registry
        self.mixer = mixer
        self.clock = clock
        self.clients = []
        self.error = ''
        self.audio_client = None
        self.audio_process = None
        self._listener = None
        self._discovery = None
        self._sources = []
        self._state_source = None
        self._commands = {
            'volume': self._on_volume,
            'mute': self._on_mute,
            'route': self._on_route,
            'app_volume': self._on_app_volume,
            'app_mute': self._on_app_mute,
            'audio': self._on_audio,
            'ping': self._on_ping,
        }

    #
    # # Running
    #
    @property
    def running(self):
        return self._listener is not None

    @property
    def mic_port(self):
        return self.config.remote_port + 1

    def set_enabled(self, state):
        self.config.remote_enabled = state
        if state:
            self.start()
        else:
            self.stop()

    def start(self):
        '''Start listening, returns if it does. Why not is in "error".'''
        if self.running:
            return True

        # One a crash left behind would go on playing to a phone nobody controls it from
        pw_phone.stop()

        port = self.config.remote_port
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('0.0.0.0', port))
            listener.listen(MAX_CLIENTS)
            listener.setblocking(False)
            discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            discovery.bind(('0.0.0.0', port))
            discovery.setblocking(False)
        except OSError as e:
            listener.close()
            discovery.close()
            self.error = e.strerror or str(e)
            LOG.error('The phone app cannot connect, port %s: %s', port, self.error)
            self.emit('changed')
            return False

        self.error = ''
        self._listener = listener
        self._discovery = discovery
        self._sources = [
            GLib.io_add_watch(listener.fileno(), GLib.IO_IN, self._on_incoming),
            GLib.io_add_watch(discovery.fileno(), GLib.IO_IN, self._on_discovery),
            GLib.timeout_add(CHECK_INTERVAL_MS, self._check_clients),
        ]
        LOG.info('Listening for the phone app on port %s', port)
        self.emit('changed')
        return True

    def stop(self):
        '''Stop listening and drop every phone, which also ends the audio of the one that had it.'''
        for client in list(self.clients):
            self._drop(client)
        self._stop_audio()

        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []
        for sock in (self._listener, self._discovery):
            if sock is not None:
                sock.close()
        self._listener = None
        self._discovery = None
        self.emit('changed')

    def forget(self, phone_id):
        '''Take back what a phone was allowed. It has to ask again.'''
        self.config.remote_phones.pop(phone_id, None)
        for client in [client for client in self.clients if client.phone_id == phone_id]:
            self._send(client, {'t': 'denied'})
            self._drop(client)
        self.emit('changed')

    def connected_ids(self):
        return {client.phone_id for client in self.clients if client.authed}

    #
    # # Sockets
    #
    def _on_discovery(self, *_):
        try:
            question, sender = self._discovery.recvfrom(256)
        except OSError:
            return GLib.SOURCE_CONTINUE

        if question.strip() == DISCOVERY_QUESTION:
            answer = {'t': 'neomixer', 'name': socket.gethostname(), 'port': self.config.remote_port, 'v': PROTOCOL}
            try:
                self._discovery.sendto(json.dumps(answer).encode(), sender)
            except OSError:
                pass
        return GLib.SOURCE_CONTINUE

    def _on_incoming(self, *_):
        try:
            sock, (address, _port) = self._listener.accept()
        except OSError:
            return GLib.SOURCE_CONTINUE

        if len(self.clients) >= MAX_CLIENTS:
            sock.close()
            return GLib.SOURCE_CONTINUE

        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client = Client(sock, address)
        client.last_heard = self.clock()
        client.watches['in'] = GLib.io_add_watch(
            sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_readable, client
        )
        self.clients.append(client)
        return GLib.SOURCE_CONTINUE

    def _on_readable(self, _fd, _condition, client):
        try:
            data = client.sock.recv(4096)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError:
            data = b''

        if not data:
            client.watches.pop('in', None)
            self._drop(client)
            return GLib.SOURCE_REMOVE

        client.last_heard = self.clock()
        client.unread += data
        while b'\n' in client.unread and client in self.clients:
            line, _, client.unread = client.unread.partition(b'\n')
            self.handle_line(client, line)

        if len(client.unread) > MAX_LINE_BYTES and client in self.clients:
            client.watches.pop('in', None)
            self._drop(client)
            return GLib.SOURCE_REMOVE

        # dropped while handling its lines: the watch is gone with it
        return GLib.SOURCE_CONTINUE if client in self.clients else GLib.SOURCE_REMOVE

    def _send(self, client, message):
        if client.sock is None:
            return
        client.unsent += json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
        self._flush(client)

    def _flush(self, client):
        try:
            sent = client.sock.send(client.unsent)
            client.unsent = client.unsent[sent:]
        except BlockingIOError:
            pass
        except OSError:
            client.unsent = b''
            return

        if len(client.unsent) > MAX_UNSENT_BYTES:
            # It stopped reading. Dropped from the main loop, not from inside whatever is sending.
            client.unsent = b''
            GLib.idle_add(self._drop_later, client)
        elif client.unsent and 'out' not in client.watches:
            client.watches['out'] = GLib.io_add_watch(client.sock.fileno(), GLib.IO_OUT, self._on_writable, client)

    def _on_writable(self, _fd, _condition, client):
        if client.sock is not None:
            self._flush(client)
        if client.sock is not None and client.unsent:
            return GLib.SOURCE_CONTINUE
        client.watches.pop('out', None)
        return GLib.SOURCE_REMOVE

    def _drop_later(self, client):
        self._drop(client)
        return GLib.SOURCE_REMOVE

    def _drop(self, client):
        if client not in self.clients:
            return

        self.clients.remove(client)
        for source in client.watches.values():
            GLib.source_remove(source)
        client.watches = {}
        if client.sock is not None:
            try:
                client.sock.close()
            except OSError:
                pass
            client.sock = None

        if client is self.audio_client:
            self._stop_audio()
        if client.authed:
            LOG.info('%s disconnected', client.name)
            self.emit('changed')
        self._schedule_state()

    def _check_clients(self):
        now = self.clock()
        for client in list(self.clients):
            if now - client.last_heard > CLIENT_TIMEOUT_SECONDS and not client.asking:
                self._drop(client)
        return GLib.SOURCE_CONTINUE

    #
    # # What a phone says
    #
    def handle_line(self, client, line):
        try:
            message = json.loads(line)
            kind = message['t']
        except (ValueError, KeyError, TypeError):
            return

        if not client.authed:
            self._handshake(client, kind, message)
            return

        handler = self._commands.get(kind)
        if handler is None:
            return
        try:
            handler(client, message)
        except (KeyError, TypeError, ValueError, AttributeError):
            # A device that is gone since the phone's last state, or a message that makes no sense
            LOG.debug('Ignoring %s from %s', message, client.name)

    def _handshake(self, client, kind, message):
        if kind == 'hello':
            phone_id = str(message.get('id', ''))[:64]
            if not phone_id or client.phone_id is not None:
                return
            client.phone_id = phone_id
            client.name = str(message.get('name') or 'Phone')[:64]
            if phone_id in self.config.remote_phones:
                client.nonce = secrets.token_hex(16)
                self._send(client, {'t': 'challenge', 'nonce': client.nonce})
            else:
                self._send(client, {'t': 'pair'})

        elif kind == 'auth' and client.nonce is not None:
            phone = self.config.remote_phones.get(client.phone_id)
            nonce, client.nonce = client.nonce, None
            if phone is not None and hmac.compare_digest(answer_to(phone.secret, nonce), str(message.get('mac', ''))):
                phone.name = client.name
                self._welcome(client)
            else:
                self._send(client, {'t': 'denied'})
                self._drop(client)

        elif kind == 'pair' and client.phone_id is not None and client.nonce is None and not client.asking:
            secret = str(message.get('secret', ''))
            try:
                valid = len(bytes.fromhex(secret)) >= 16
            except ValueError:
                valid = False
            if not valid or client.phone_id in self.config.remote_phones:
                self._send(client, {'t': 'denied'})
                self._drop(client)
                return

            client.asking = True
            self.emit('pair_request', client.name, client.address, lambda allowed: self._on_pair_answer(client, secret, allowed))

    def _on_pair_answer(self, client, secret, allowed):
        client.asking = False
        if client not in self.clients:
            return

        if not allowed:
            self._send(client, {'t': 'denied'})
            self._drop(client)
            return

        self.config.remote_phones[client.phone_id] = RemotePhone(name=client.name, secret=secret)
        client.last_heard = self.clock()
        self._welcome(client)

    def _welcome(self, client):
        client.authed = True
        LOG.info('%s connected from %s', client.name, client.address)
        self._send(client, {'t': 'welcome', 'name': socket.gethostname(), 'version': VERSION, 'v': PROTOCOL})
        self._send_state(client)
        self._schedule_state()
        self.emit('changed')

    def _on_ping(self, client, _message):
        self._send(client, {'t': 'pong'})

    def _on_volume(self, _client, message):
        volume = min(max(int(message['value']), 0), MAX_VOLUME)
        self.mixer.apply_volume(str(message['type']), str(message['id']), volume)

    def _on_mute(self, _client, message):
        self.mixer.apply_mute(str(message['type']), str(message['id']), bool(message['value']))

    def _on_route(self, _client, message):
        input_type, output_type = str(message['in_type']), str(message['out_type'])
        if input_type in INPUT_TYPES and output_type in OUTPUT_TYPES:
            self.mixer.apply_route(input_type, str(message['in_id']), output_type, str(message['out_id']), bool(message['value']))

    def _on_app_volume(self, _client, message):
        volume = min(max(int(message['value']), 0), MAX_VOLUME)
        self.mixer.apply_app_volume(str(message['app_type']), str(message['key']), volume)

    def _on_app_mute(self, _client, message):
        self.mixer.apply_app_mute(str(message['app_type']), str(message['key']), bool(message['value']))

    #
    # # State
    #
    def snapshot(self):
        '''Everything a phone shows.'''
        devices = self.device_repository.get_all_devices()
        ordered = {
            device_type: sorted(devices[device_type].items(), key=lambda item: int(item[0]))
            for device_type in DEVICE_ORDER
        }

        strips = []
        for device_type in DEVICE_ORDER:
            for position, (device_id, device) in enumerate(ordered[device_type]):
                strip = {
                    'type': device_type,
                    'id': device_id,
                    'name': device.nick,
                    'volume': device.volume[0] if device.volume else 100,
                    'mute': bool(device.mute),
                    'present': bool(device.present and not device.init_failed),
                }
                if device.name in pw_phone.NODE_NAMES:
                    strip['phone'] = True
                if device_type in OUTPUT_TYPES:
                    strip['tag'] = f'{device_type.upper()}{position + 1}'
                else:
                    strip['routes'] = [
                        f'{output_type}{output_id}'
                        for output_type in OUTPUT_TYPES
                        for output_id, _device in ordered[output_type]
                        if (connection := device.connections[output_type].get(output_id)) is not None and connection.state
                    ]
                strips.append(strip)

        apps = [
            {
                'app_type': app_type,
                'key': entry.key,
                'name': entry.label,
                'volume': int(entry.volume),
                'mute': bool(entry.mute),
                'state': entry.state,
            }
            for app_type in APP_TYPES
            for entry in sorted(self.app_registry.entries[app_type].values(), key=lambda entry: entry.label.lower())
        ]

        return {'t': 'state', 'strips': strips, 'apps': apps}

    def _schedule_state(self):
        '''The state is compared only while a phone is there to see it.'''
        watched = any(client.authed for client in self.clients)
        if watched and self._state_source is None:
            self._state_source = GLib.timeout_add(STATE_INTERVAL_MS, self._send_states)
        elif not watched and self._state_source is not None:
            GLib.source_remove(self._state_source)
            self._state_source = None

    def _send_states(self):
        try:
            state = json.dumps(self.snapshot(), ensure_ascii=False, separators=(',', ':'))
            for client in list(self.clients):
                if client.authed:
                    self._send_state(client, state)
        except Exception:
            LOG.exception('Could not send the state to the phones')
        return GLib.SOURCE_CONTINUE

    def _send_state(self, client, state=None):
        if state is None:
            state = json.dumps(self.snapshot(), ensure_ascii=False, separators=(',', ':'))
        if state == client.sent_state or client.sock is None:
            return
        client.sent_state = state
        client.unsent += state.encode() + b'\n'
        self._flush(client)

    #
    # # Audio
    #
    def _on_audio(self, client, message):
        if not message.get('on'):
            if client is self.audio_client:
                self._stop_audio()
            self._send(client, {'t': 'audio', 'on': False})
            return

        port = int(message['port'])
        if not 1024 <= port <= 65535:
            return

        # One phone at a time: there is one pair of devices
        previous = self.audio_client
        self._stop_audio()
        if previous is not None and previous is not client:
            self._send(previous, {'t': 'audio', 'on': False, 'error': 'taken'})

        try:
            self.audio_process = pw_phone.start(client.address, port, self.mic_port, client.name)
        except OSError as e:
            LOG.error('Could not start the phone audio: %s', e)
            self._send(client, {'t': 'audio', 'on': False, 'error': str(e)})
            return

        self.audio_client = client
        GLib.timeout_add(AUDIO_START_INTERVAL_MS, self._check_audio_started, self.audio_process, AUDIO_START_CHECKS)

    def _check_audio_started(self, process, checks_left):
        if process is not self.audio_process:
            return GLib.SOURCE_REMOVE

        started = process.poll() is None and all(pmctl.device_exists(name) for name in pw_phone.NODE_NAMES)
        if not started and process.poll() is None and checks_left > 0:
            GLib.timeout_add(AUDIO_START_INTERVAL_MS, self._check_audio_started, process, checks_left - 1)
            return GLib.SOURCE_REMOVE

        client = self.audio_client
        if not started:
            error = pw_phone.last_error() or 'The phone devices did not start'
            LOG.error('The phone audio did not start: %s', error)
            self._stop_audio()
            self._send(client, {'t': 'audio', 'on': False, 'error': error})
            return GLib.SOURCE_REMOVE

        self._ensure_devices(client.name)
        self._send(client, {
            't': 'audio', 'on': True, 'mic_port': self.mic_port, 'rate': pw_phone.RATE,
            'channels': pw_phone.SINK_CHANNELS, 'mic_channels': pw_phone.SOURCE_CHANNELS,
            'packet_ms': pw_phone.PACKET_MS, 'payload': pw_phone.PAYLOAD_TYPE,
        })
        return GLib.SOURCE_REMOVE

    def _ensure_devices(self, phone_name):
        '''
        The first time a phone plays, its two devices get their strips. After that they are
        there like any hardware that comes and goes, with the routes they were given.
        '''
        for name, nick, pa_type, channels, positions in PHONE_DEVICES:
            bucket = 'a' if pa_type == 'sink' else 'hi'
            if self.device_repository.find_device_by_key('name', name, (bucket,)):
                continue
            self.emit('device_new', {
                'name': name,
                'nick': nick,
                'description': phone_name if pa_type == 'sink' else f'{phone_name} microphone',
                'device_type': pa_type,
                'device_class': 'hardware',
                'channels': channels,
                'channel_list': positions,
                'selected_channels': [True] * channels,
                'volume': [100] * channels,
                'mute': False,
            })

    def _stop_audio(self):
        process, self.audio_process = self.audio_process, None
        self.audio_client = None
        if process is not None:
            pw_phone.stop(process)

