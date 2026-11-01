'''
Global shortcuts through the desktop's portal. On Wayland a program never sees keys pressed in other
windows, it can only ask the desktop to tell it when a shortcut is used. The desktop owns the keys:
it asks the user about new shortcuts, and the keys can be changed in its settings.

Everything here is asynchronous. The portal may be slow to start at login, or wait for a dialog, and
that must not hold up the mixer.
'''
import logging

from gi.repository import Gio, GLib

LOG = logging.getLogger('generic')

PORTAL = 'org.freedesktop.portal.Desktop'
PATH = '/org/freedesktop/portal/desktop'
SHORTCUTS = 'org.freedesktop.portal.GlobalShortcuts'
REGISTRY = 'org.freedesktop.host.portal.Registry'
REQUEST = 'org.freedesktop.portal.Request'
SESSION = 'org.freedesktop.portal.Session'
KGLOBALACCEL = 'org.kde.kglobalaccel'

RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1
RESPONSE_FAILED = 2

RETRY_SECONDS = 5
MAX_RETRIES = 3


def _triggers(results):
    '''The keys the desktop has for each shortcut, out of what it answered.'''
    return {shortcut: info.get('trigger_description', '') for shortcut, info in results.get('shortcuts', [])}


class ShortcutsPortal:
    '''
    A connection of its own: the portal goes by the first thing it hears from a connection to tell
    which application this is, and GTK has already talked to it on the shared one.
    '''

    def __init__(self, app_id, pressed, released, changed, restarted):
        '''
            "pressed", "released" are called with a shortcut's id
            "changed" is called with {id: keys}, when the keys changed in the desktop's settings
            "restarted" is called when a new portal took over, which knows nothing of this one
        '''
        self.app_id = app_id
        self.pressed = pressed
        self.released = released
        self.changed = changed
        self.restarted = restarted
        self.connection = None
        self.sender = None
        self.session = None
        self.version = 0  # of the interface, 0 when the desktop has none
        self.opened = False
        self._waiting = []
        self._requests = {}
        self._serial = 0
        self._retries = 0
        self._gone = False

    def open(self, ready):
        '''Connect, and call "ready" when "version" says if this desktop can do it.'''
        if self.opened:
            ready()
            return

        self._waiting.append(ready)
        if len(self._waiting) > 1:
            return

        try:
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        except GLib.Error as error:
            LOG.warning('No session bus for global shortcuts: %s', error.message)
            self._opened()
            return

        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        Gio.DBusConnection.new_for_address(address, flags, None, None, self._on_connected)

    def _on_connected(self, _, result):
        try:
            self.connection = Gio.DBusConnection.new_for_address_finish(result)
        except GLib.Error as error:
            LOG.warning('No session bus for global shortcuts: %s', error.message)
            self._opened()
            return

        self.connection.set_exit_on_close(False)
        self.sender = self.connection.get_unique_name()[1:].replace('.', '_')
        for name, callback in (('Activated', self._on_activated), ('Deactivated', self._on_deactivated),
                               ('ShortcutsChanged', self._on_shortcuts_changed)):
            self.connection.signal_subscribe(PORTAL, SHORTCUTS, name, PATH, None, Gio.DBusSignalFlags.NONE, callback)
        self.connection.signal_subscribe('org.freedesktop.DBus', 'org.freedesktop.DBus', 'NameOwnerChanged', None, PORTAL,
                                         Gio.DBusSignalFlags.NONE, self._on_owner_changed)
        self._register(lambda: self._read_version(self._opened))

    def _opened(self):
        self.opened = True
        waiting, self._waiting = self._waiting, []
        for ready in waiting:
            ready()

    def _register(self, done):
        '''
        Say which application this is. The desktop files its shortcuts under that, without it they
        are gone by the next start. An older portal does not have this, and looks at the process.
        '''
        def on_reply(_, error):
            if error is not None:
                LOG.info('Could not register with the portal: %s', error.message)
            done()

        self._call(REGISTRY, 'Register', GLib.Variant('(sa{sv})', (self.app_id, {})), on_reply)

    def _read_version(self, done):
        def on_reply(reply, error):
            self.version = reply.unpack()[0] if error is None else 0
            if error is not None:
                LOG.info('This desktop has no global shortcuts: %s', error.message)
            done()

        self._call('org.freedesktop.DBus.Properties', 'Get', GLib.Variant('(ss)', (SHORTCUTS, 'version')), on_reply)

    def _call(self, interface, method, arguments, on_reply, path=PATH):
        def on_done(connection, result):
            try:
                reply = connection.call_finish(result)
            except GLib.Error as error:
                on_reply(None, error)
                return
            on_reply(reply, None)

        self.connection.call(PORTAL, path, interface, method, arguments, None, Gio.DBusCallFlags.NONE, -1, None, on_done)

    def _request(self, method, arguments, on_response):
        '''
        The answer to a request is a signal on an object named after its token, which has to be
        listened to before asking.
            "arguments" gets the token's options and returns the method's arguments
        '''
        self._serial += 1
        token = f'neomixer{self._serial}'
        path = f'{PATH}/request/{self.sender}/{token}'

        def finish(response, results):
            subscription = self._requests.pop(token, None)
            if subscription is None:
                return
            self.connection.signal_unsubscribe(subscription[0])
            on_response(response, results)

        def on_signal(_connection, _sender, _path, _interface, _signal, parameters):
            finish(*parameters.unpack())

        def on_reply(_, error):
            if error is not None:
                LOG.warning('%s failed: %s', method, error.message)
                finish(RESPONSE_FAILED, {})

        subscription = self.connection.signal_subscribe(PORTAL, REQUEST, 'Response', path, None,
                                                        Gio.DBusSignalFlags.NONE, on_signal)
        self._requests[token] = (subscription, finish)
        self._call(SHORTCUTS, method, arguments({'handle_token': GLib.Variant('s', token)}), on_reply)

    def bind(self, shortcuts, done):
        '''
        Make these the shortcuts of this application, the desktop asks about the new ones. A session
        can only do this once, so it takes a new one each time.
            "shortcuts" is {id: (description, preferred keys)}
            "done" is called with the response and {id: keys}
        '''
        self.close_session()

        def on_session(response, results):
            if response != RESPONSE_SUCCESS:
                done(response, {})
                return
            self.session = results['session_handle']
            self._request('ListShortcuts', lambda options: GLib.Variant('(oa{sv})', (self.session, options)), on_list)

        def on_list(response, results):
            known = _triggers(results) if response == RESPONSE_SUCCESS else {}
            self._forget([shortcut for shortcut in known if shortcut not in shortcuts], on_forgotten)

        def on_forgotten():
            if shortcuts:
                self._bind(shortcuts, done)
            else:
                # Older portals crash on an empty list, and there is nothing to listen for
                self.close_session()
                done(RESPONSE_SUCCESS, {})

        def arguments(options):
            options['session_handle_token'] = GLib.Variant('s', f'neomixer{self._serial}')
            return GLib.Variant('(a{sv})', (options,))

        self._request('CreateSession', arguments, on_session)

    def _bind(self, shortcuts, done):
        entries = [
            (shortcut, {'description': GLib.Variant('s', description), 'preferred_trigger': GLib.Variant('s', trigger)})
            for shortcut, (description, trigger) in shortcuts.items()
        ]

        def on_bound(response, results):
            if response == RESPONSE_SUCCESS:
                self._retries = 0
            done(response, _triggers(results))

        self._request(
            'BindShortcuts', lambda options: GLib.Variant('(oa(sa{sv})sa{sv})', (self.session, entries, '', options)), on_bound
        )

    def _forget(self, shortcuts, done):
        '''
        The portal cannot say this. KDE keeps all it ever heard of with their keys, whatever is bound
        later: a new shortcut could not get those keys, and one that was refused is remembered as
        having no keys and never asked about again. Elsewhere there is no such service to call.
        '''
        waiting = len(shortcuts)
        if not waiting:
            done()
            return

        def on_reply(connection, result):
            nonlocal waiting
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                LOG.debug('Could not remove a shortcut: %s', error.message)
            waiting -= 1
            if not waiting:
                done()

        for shortcut in shortcuts:
            self.connection.call(KGLOBALACCEL, '/kglobalaccel', 'org.kde.KGlobalAccel', 'unregister',
                                 GLib.Variant('(ss)', (self.app_id, shortcut)), None,
                                 Gio.DBusCallFlags.NO_AUTO_START, -1, None, on_reply)

    def configure(self):
        '''Open the desktop's own settings for these shortcuts, if it can.'''
        if self.session is None or self.version < 2:
            return
        self._call(SHORTCUTS, 'ConfigureShortcuts', GLib.Variant('(osa{sv})', (self.session, '', {})),
                   lambda _, error: error is not None and LOG.warning('ConfigureShortcuts failed: %s', error.message))

    def close_session(self):
        if self.session is None:
            return
        self._call(SESSION, 'Close', None, lambda *_: None, path=self.session)
        self.session = None

    def _is_ours(self, session):
        return session == self.session and session is not None

    def _on_activated(self, _connection, _sender, _path, _interface, _signal, parameters):
        session, shortcut, *_ = parameters.unpack()
        if self._is_ours(session):
            self.pressed(shortcut)

    def _on_deactivated(self, _connection, _sender, _path, _interface, _signal, parameters):
        session, shortcut, *_ = parameters.unpack()
        if self._is_ours(session):
            self.released(shortcut)

    def _on_shortcuts_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        session, shortcuts = parameters.unpack()
        if self._is_ours(session):
            self.changed(_triggers({'shortcuts': shortcuts}))

    def _on_owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        _, previous, owner = parameters.unpack()
        if owner:
            # Asking it the first time is also what starts it, that is no new portal. There may
            # have been none at first, though.
            if previous or self._gone or (self.opened and self.version == 0):
                self._gone = False
                self._register(lambda: self._read_version(self.restarted))
            return

        # What was asked of it will not get an answer. It may not come back by itself.
        self._gone = True
        had_session = self.session is not None
        self.session = None
        for _, finish in list(self._requests.values()):
            finish(RESPONSE_FAILED, {})
        if had_session and self._retries < MAX_RETRIES:
            self._retries += 1
            GLib.timeout_add_seconds(RETRY_SECONDS, self._wake)

    def _wake(self):
        '''Any call starts it again.'''
        if self._gone:
            self._call('org.freedesktop.DBus.Peer', 'Ping', None, lambda *_: None)
        return GLib.SOURCE_REMOVE
