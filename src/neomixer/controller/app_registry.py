import os
import time
import logging

import pulsectl

from neomixer.scripts import pmctl
from neomixer.model.app_model import AppModel
from neomixer.model.known_app import KnownApp
from neomixer.model.signal_model import SignalModel
from neomixer.utils.app_identity import app_key, app_label, better_label, is_system_client

LOG = logging.getLogger("generic")

# Until a new stream has been around this long, what it does is the session manager still
# placing it: it is corrected. After that a change was made by someone: it is remembered.
SETTLE_SECONDS = 2.0


class AppEntry:
    '''
    One program in the app list. Its streams come and go, and it stays.
    '''

    def __init__(self, app_type, known):
        self.app_type = app_type
        self.known = known
        self.streams = {}
        self.stream_ages = {}
        self.clients = set()

    @property
    def key(self):
        return self.known.key

    @property
    def label(self):
        return self.known.label

    @property
    def icon(self):
        return self.known.icon

    @property
    def binary(self):
        return self.known.binary

    @property
    def state(self):
        '''
        "active" with an open stream, "idle" while connected to the sound server,
        "offline" while it is only remembered
        '''
        if self.streams:
            return 'active'
        return 'idle' if self.clients else 'offline'

    @property
    def volume(self):
        if self.known.volume is not None:
            return self.known.volume
        return next((stream.volume for stream in self.streams.values()), 100)

    @property
    def mute(self):
        if self.known.mute is not None:
            return self.known.mute
        return next((stream.mute for stream in self.streams.values()), False)

    @property
    def device(self):
        '''
        Device shown in the selector, '' for following the default: the choice if there is
        one, a stream can be elsewhere for a moment or while its device is missing.
        '''
        if self.known.device is not None:
            return self.known.device
        for stream in self.streams.values():
            return stream.display_device
        return ''

    @property
    def movable(self):
        return all(stream.movable for stream in self.streams.values())


class AppRegistry(SignalModel):
    '''
    Keeps the list of programs, and applies what is remembered about a program to its streams
    whenever they appear: when it starts, or restarts, or after a reboot. Everything runs on
    the main thread.

    Signals:
        entry_new(entry: AppEntry): A program was added to the list.
        entry_change(entry: AppEntry): A program, or the state of one of its streams, changed.
        entry_remove(app_type: str, key: str): A program was removed from the list.
    '''

    def __init__(self, device_repository, clock=time.monotonic):
        super().__init__()
        self.device_repository = device_repository
        self.known_apps = device_repository.config.apps
        self.clock = clock
        self.entries = {'sink_input': {}, 'source_output': {}}
        self._stream_keys = {'sink_input': {}, 'source_output': {}}
        self._client_keys = {}
        self._announced = False

        for app_type, known_apps in self.known_apps.items():
            for key, known in known_apps.items():
                self.entries[app_type][key] = AppEntry(app_type, known)

    def bootstrap(self):
        '''
        Read what is running right now, and put it where it belongs. For startup, and after
        the sound server restarted.
        '''
        for entries in self.entries.values():
            for entry in entries.values():
                entry.streams.clear()
                entry.stream_ages.clear()
                entry.clients.clear()
                self.emit('entry_change' if self._announced else 'entry_new', entry)
        self._announced = True
        self._stream_keys = {'sink_input': {}, 'source_output': {}}
        self._client_keys = {}

        for client in pmctl.list_clients():
            self.client_new(client.index, client.proplist)

        pins = pmctl.get_app_pins()
        for app_type in ('sink_input', 'source_output'):
            for app in pmctl.list_apps(app_type):
                self.stream_new(app_type, app.index, AppModel.pa_to_app_model(app, app_type), pins)

    #
    # # What the sound server reports
    #
    def stream_new(self, app_type, index, app, pins=None):
        if index in self._stream_keys[app_type]:
            self.stream_change(app_type, index, app)
            return

        self._mark_pinned(app, pins)
        entry, created = self._entry_for(app_type, app.key, app.label, app.icon, app.binary)
        entry.streams[index] = app
        entry.stream_ages[index] = self.clock()
        entry.known.last_seen = time.time()
        self._stream_keys[app_type][index] = app.key

        self._apply_rule(entry, app)
        self.emit('entry_new' if created else 'entry_change', entry)

    def stream_change(self, app_type, index, app):
        key = self._stream_keys[app_type].get(index)
        if key is None:
            self.stream_new(app_type, index, app)
            return

        entry = self.entries[app_type][key]
        old = entry.streams[index]
        moved = old.device != app.device
        if moved:
            self._mark_pinned(app)
        else:
            # only a move changes the pin, no need to look it up for every volume step
            app.pinned = old.pinned

        entry.streams[index] = app
        if self.clock() - entry.stream_ages[index] < SETTLE_SECONDS:
            self._apply_rule(entry, app)
        else:
            self._remember_changes(entry, old, app, moved)

        self.emit('entry_change', entry)

    def stream_remove(self, app_type, index):
        key = self._stream_keys[app_type].pop(index, None)
        if key is None:
            return

        entry = self.entries[app_type][key]
        entry.streams.pop(index, None)
        entry.stream_ages.pop(index, None)
        entry.known.last_seen = time.time()
        self.emit('entry_change', entry)

    def client_new(self, index, props):
        if is_system_client(props) or props.get('application.process.id') == str(os.getpid()):
            return

        key = app_key(props)
        entry, created = self._entry_for(
            'sink_input', key, app_label(props), props.get('application.icon_name'),
            props.get('application.process.binary')
        )
        entry.clients.add(index)
        entry.known.last_seen = time.time()
        self._client_keys[index] = key
        self.emit('entry_new' if created else 'entry_change', entry)

    def client_remove(self, index):
        key = self._client_keys.pop(index, None)
        if key is None:
            return

        entry = self.entries['sink_input'].get(key)
        if entry is not None:
            entry.clients.discard(index)
            self.emit('entry_change', entry)

    def device_appeared(self):
        '''
        A device came (back): streams that belong on it were left elsewhere until now.
        '''
        for entries in self.entries.values():
            for entry in entries.values():
                for stream in entry.streams.values():
                    self._apply_rule(entry, stream)

    #
    # # What the user asks for
    #
    def set_device(self, app_type, key, device_name):
        entry = self.entries[app_type].get(key)
        if entry is None:
            return

        entry.known.device = device_name
        for stream in entry.streams.values():
            self._pin(entry, stream, device_name)
            stream.pinned = bool(device_name)
        self.emit('entry_change', entry)

    def set_volume(self, app_type, key, volume):
        entry = self.entries[app_type].get(key)
        if entry is None:
            return

        entry.known.volume = int(volume)
        for index, stream in entry.streams.items():
            stream.volume = int(volume)
            self._safely(pmctl.app_volume, app_type, index, int(volume))

    def set_mute(self, app_type, key, state):
        entry = self.entries[app_type].get(key)
        if entry is None:
            return

        entry.known.mute = state
        for index, stream in entry.streams.items():
            stream.mute = state
            self._safely(pmctl.app_mute, app_type, index, state)

    def forget(self, app_type, key):
        '''Remove a program from the list. It comes back when it next connects, without its settings.'''
        entry = self.entries[app_type].get(key)
        if entry is None or entry.state != 'offline':
            return False

        del self.entries[app_type][key]
        self.known_apps[app_type].pop(key, None)
        self.emit('entry_remove', app_type, key)
        return True

    def add_manually(self, app_type, key, label, icon=None, binary=None):
        '''List a program that has never been seen, so that it can be set up beforehand.'''
        entry, created = self._entry_for(app_type, key, label, icon, binary)
        if created:
            self.emit('entry_new', entry)
        return entry

    #
    # # Internals
    #
    def _entry_for(self, app_type, key, label, icon, binary):
        entry = self.entries[app_type].get(key)
        if entry is not None:
            known = entry.known
            known.label = better_label(known.label, label, key)
            known.icon = icon or known.icon
            known.binary = binary or known.binary
            return entry, False

        known = KnownApp(key=key, label=label, icon=icon, binary=binary, last_seen=time.time())
        self.known_apps[app_type][key] = known
        entry = self.entries[app_type][key] = AppEntry(app_type, known)
        return entry, True

    def _apply_rule(self, entry, stream):
        '''Put a stream where its program belongs, it does not know by itself.'''
        known = entry.known
        if known.device is not None and stream.movable:
            at_target = stream.device == known.device if known.device else not stream.pinned
            if (not at_target or (known.device and not stream.pinned)) and self._target_exists(entry, known.device):
                self._pin(entry, stream, known.device)
                stream.pinned = bool(known.device)

        if known.volume is not None and stream.volume != known.volume:
            self._safely(pmctl.app_volume, entry.app_type, stream.index, known.volume)
            stream.volume = known.volume

        if known.mute is not None and stream.mute != known.mute:
            self._safely(pmctl.app_mute, entry.app_type, stream.index, known.mute)
            stream.mute = known.mute

    def _remember_changes(self, entry, old, app, moved):
        '''
        Someone changed a stream from outside NeoMixer, such as a volume applet or the app's
        own volume slider: that is what it should have from now on.
        '''
        known = entry.known
        if app.volume != old.volume:
            known.volume = app.volume
        if app.mute != old.mute:
            known.mute = app.mute

        # A stream also moves when its device goes away, without anyone wanting that. It is
        # only a choice if it was pinned to the new place while the old one is still there.
        if not moved or not app.pinned or app.device == known.device:
            return
        if known.device and not self._target_exists(entry, known.device):
            return
        if self.device_repository.find_device_by_key('name', self._device_base_name(app.device)):
            known.device = app.device

    @staticmethod
    def _device_base_name(device_name):
        return device_name.removesuffix('.monitor')

    def _target_exists(self, entry, device_name):
        if not device_name:
            return True
        device_type = 'sink' if entry.app_type == 'sink_input' else 'source'
        return pmctl.get_device_by_name(device_type, device_name) is not None

    def _pin(self, entry, stream, device_name):
        if stream.object_id is None:
            return
        device_type = 'sink' if entry.app_type == 'sink_input' else 'source'
        if device_name:
            pmctl.pin_app(stream.object_id, device_type, device_name)
        else:
            pmctl.unpin_app(stream.object_id)

    @staticmethod
    def _mark_pinned(app, pins=None):
        '''
        Set app.pinned: the "default" metadata decides if it has an entry for the app, else
        whether the app named a device itself. No metadata means pw-dump is unavailable: treat
        the app as pinned, so the selector shows its resolved device.
        '''
        if pins is None:
            pins = pmctl.get_app_pins()
        app.pinned = pins is None or pins.get(app.object_id, app.has_target)

    @staticmethod
    def _safely(function, *args):
        '''A stream can be gone by the time it is changed, which is not a problem.'''
        try:
            function(*args)
        except (pulsectl.PulseError, pulsectl.PulseDisconnected) as e:
            LOG.debug('Could not update a stream: %r', e)
