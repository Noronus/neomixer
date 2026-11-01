'''
Hotkeys: which shortcuts the desktop should have, and what happens when one is used.
'''
import gettext
import logging
import os
import time

from neomixer.model.signal_model import SignalModel
from neomixer.settings import APP_ID
from neomixer.utils.hotkeys import ACTIONS, parse_shortcut_id, shortcut_id
from neomixer.utils.shortcuts_portal import RESPONSE_CANCELLED, RESPONSE_SUCCESS, ShortcutsPortal

from gi.repository import GLib

LOG = logging.getLogger('generic')

_ = gettext.gettext

ACTION_LABELS = {'mute': _('Mute'), 'up': _('Louder'), 'down': _('Softer')}

REPEAT_DELAY_MS = 400
REPEAT_EVERY_MS = 100
REPEAT_MAX_SECONDS = 10

# A desktop may send a held key again and again, without saying it was released in between
SAME_PRESS_SECONDS = 0.3

# Their portals are known to say when a key is released. Elsewhere that has to be seen first: a key
# that never reports its release would keep on turning the volume up.
RELEASE_KNOWN_ON = ('KDE', 'GNOME')

NOTICE_APPLIED = 'applied'
NOTICE_CANCELLED = 'cancelled'
NOTICE_FAILED = 'failed'


class HotkeyController(SignalModel):
    '''
    Signals:
        state_changed(controller): what the hotkey window shows has changed
    '''

    def __init__(self, device_repository, perform):
        '''
            "perform" does an action, called with (device_type, device_id, action, held), and
            returns if a held key can go on
        '''
        super().__init__()
        self.device_repository = device_repository
        self.perform = perform
        self.portal = ShortcutsPortal(APP_ID, self._on_pressed, self._on_released, self._on_changed, self._on_restarted)
        self.bound = {}  # id: the keys, as the desktop shows them
        self.busy = False  # waiting for the desktop, which may be asking the user
        self.notice = None
        self._asked = False  # the desktop has heard from this run, so it may have something to forget
        self._synced = None
        self._sync_again = False
        self._held = {}
        self._repeat_source = None
        desktops = os.environ.get('XDG_CURRENT_DESKTOP', '').split(':')
        self._release_seen = any(desktop in RELEASE_KNOWN_ON for desktop in desktops)

    @property
    def available(self):
        '''If this desktop has global shortcuts, None until that is known.'''
        return self.portal.version > 0 if self.portal.opened else None

    @property
    def can_configure(self):
        return self.portal.version >= 2 and self.portal.session is not None

    def wanted(self):
        '''{id: (description, keys)} of all hotkeys in the config.'''
        shortcuts = {}
        for device_type, devices in self.device_repository.get_all_devices().items():
            for device_id, device in devices.items():
                for action, trigger in device.hotkeys.items():
                    if action in ACTIONS:
                        description = f'{device.nick}: {ACTION_LABELS[action]}'
                        shortcuts[shortcut_id(device_type, device_id, action, trigger)] = (description, trigger)
        return shortcuts

    def start(self):
        '''Without any hotkeys the desktop's portal is left alone.'''
        if self.wanted():
            self.sync()

    def probe(self):
        '''Find out what this desktop can do, for the hotkey window.'''
        self.portal.open(lambda: self.emit('state_changed', self))

    def stop(self):
        self._stop_repeat()

    def apply(self, edits):
        '''
        Take changes from the hotkey window.
            "edits" is {(device_type, device_id, action): keys}, no keys remove it
        '''
        for (device_type, device_id, action), trigger in edits.items():
            device = self.device_repository.get_device(device_type, device_id)
            if device is None:
                continue
            if trigger:
                device.hotkeys[action] = trigger
            else:
                device.hotkeys.pop(action, None)
        self.notice = None
        self.sync()

    def sync(self):
        '''Have the desktop's shortcuts match the config, also if a device was removed or renamed.'''
        if not self._asked and not self.wanted():
            self.emit('state_changed', self)
            return

        if not self.portal.opened:
            self.portal.open(self.sync)
            return

        if self.busy:
            self._sync_again = True
            return

        wanted = self.wanted()
        if self.portal.version <= 0 or sorted(wanted.items()) == self._synced:
            self.emit('state_changed', self)
            return

        self.busy = True
        self._asked = True
        self.emit('state_changed', self)
        known = set(self.bound)
        self.portal.bind(wanted, lambda response, triggers: self._on_bound(response, triggers, wanted, known))

    def _on_bound(self, response, triggers, wanted, known):
        self.busy = False

        if response == RESPONSE_SUCCESS:
            self.bound = triggers
            refused = [shortcut for shortcut in wanted if not triggers.get(shortcut)]
        elif response == RESPONSE_CANCELLED:
            refused = [shortcut for shortcut in wanted if shortcut not in known]
        else:
            self.notice = NOTICE_FAILED
            self.emit('state_changed', self)
            self._after_bound()
            return

        for shortcut in refused:
            self._forget(shortcut)

        if refused:
            # The desktop remembers those as having no keys, and would never ask about them again
            self.notice = NOTICE_CANCELLED
            self._synced = None
            self._sync_again = True
        else:
            self._synced = sorted(wanted.items())
            if self.notice != NOTICE_CANCELLED:
                self.notice = NOTICE_APPLIED

        self.emit('state_changed', self)
        self._after_bound()

    def _after_bound(self):
        if self._sync_again:
            self._sync_again = False
            self.sync()

    def _forget(self, shortcut):
        device_type, device_id, action, trigger = parse_shortcut_id(shortcut)
        device = self.device_repository.get_device(device_type, device_id)
        if device is not None and device.hotkeys.get(action) == trigger:
            del device.hotkeys[action]

    def configure(self):
        self.portal.configure()

    def run_action(self, device_type, device_id, action):
        '''One step of an action, for keys set up in the desktop to run "neomixer hotkey".'''
        self.perform(device_type, device_id, action, False)

    def _on_changed(self, triggers):
        self.bound = triggers
        self.emit('state_changed', self)

    def _on_restarted(self):
        self._synced = None
        self.bound = {}
        if self.wanted():
            self.sync()

    def _on_pressed(self, shortcut):
        # A desktop may still have a key from before, the mixer only goes by the current ones
        if shortcut not in self.wanted():
            return

        now = time.monotonic()
        previous = self._held.get(shortcut)
        self._held[shortcut] = now
        if previous is not None and now - previous < SAME_PRESS_SECONDS:
            return

        self._stop_repeat()
        device_type, device_id, action, _trigger = parse_shortcut_id(shortcut)
        keep_going = self.perform(device_type, device_id, action, False)
        if keep_going and action != 'mute' and self._release_seen:
            deadline = now + REPEAT_MAX_SECONDS
            self._repeat_source = GLib.timeout_add(
                REPEAT_DELAY_MS, self._first_repeat, shortcut, device_type, device_id, action, deadline
            )

    def _first_repeat(self, *args):
        self._repeat_source = None
        if self._repeat(*args):
            self._repeat_source = GLib.timeout_add(REPEAT_EVERY_MS, self._repeat, *args)
        return GLib.SOURCE_REMOVE

    def _repeat(self, _shortcut, device_type, device_id, action, deadline):
        if time.monotonic() < deadline and self.perform(device_type, device_id, action, True):
            return GLib.SOURCE_CONTINUE

        self._repeat_source = None
        return GLib.SOURCE_REMOVE

    def _on_released(self, shortcut):
        self._release_seen = True
        self._held.pop(shortcut, None)
        self._stop_repeat()

    def _stop_repeat(self):
        if self._repeat_source is not None:
            GLib.source_remove(self._repeat_source)
            self._repeat_source = None
