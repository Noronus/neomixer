'''
GTK Application client for NeoMixer.
Manages application lifecycle and delegates UI logic to GtkController.
'''
import signal
import logging

from neomixer.settings import APP_ID, CONFIG_FILE, LEGACY_CONFIG_FILE
from neomixer.scripts import pmctl
from neomixer.model.config_model import ConfigModel

from neomixer.utils.config_persistence import ConfigPersistence, import_legacy_config
from neomixer.repository.device_repository import DeviceRepository
from neomixer.controller.gtk_controller import GtkController
from neomixer.controller.app_registry import AppRegistry
from neomixer.controller.device_controller import DeviceController
from neomixer.controller.event_controller import EventController
from neomixer.controller.hotkey_controller import HotkeyController
from neomixer.clients.gtk import style
from neomixer.clients.gtk.tray import TrayIcon

# pylint: disable=wrong-import-order,wrong-import-position
from gi import require_version as gi_require_version
gi_require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")

AUTOSAVE_SECONDS = 2


def on_main_thread(callback):
    '''
    Returns a version of a callback that is safe to call from the event thread.
    '''
    def wrapper(*args):
        def run():
            try:
                callback(*args)
            except Exception:
                LOG.exception('Error handling an event in %s', getattr(callback, '__name__', callback))
            return False

        GLib.idle_add(run)

    return wrapper


class GtkClient(Gtk.Application):
    '''
    GTK Application client for NeoMixer.
    Manages the application lifecycle and delegates UI logic to GtkController.
    Attributes:
        window (Gtk.Window): The main application window.
        config_model (ConfigModel): The configuration model instance.
    '''

    def __init__(self, start_hidden=False, quit_on_close=False):
        '''
        Initialize the GtkClient application. The audio graph is only touched once this is
        known to be the first instance, see do_startup.
        '''
        super().__init__(application_id=APP_ID)
        self.start_hidden = start_hidden
        self.quit_on_close = quit_on_close
        self.window = None
        self.tray = None
        self.gtk_controller_handlers = {}
        self.event_controller_handlers = {}
        self.device_controller_handlers = {}

    def do_startup(self, *args, **kwargs):
        '''
        Called once, in the first instance only. A second start of the app only asks this
        one to open its window.
        '''
        Gtk.Application.do_startup(self)

        import_legacy_config(CONFIG_FILE, LEGACY_CONFIG_FILE)
        self.config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
        self.config_model = self.config_persistence.get_config()
        self.device_repository = DeviceRepository(self.config_persistence)
        self.event_controller = EventController(device_repository=self.device_repository)
        self.device_controller = DeviceController(device_repository=self.device_repository)
        self.gtk_controller = GtkController(device_repository=self.device_repository)
        self.app_registry = AppRegistry(device_repository=self.device_repository)
        self.hotkey_controller = HotkeyController(self.device_repository, self.gtk_controller.perform_hotkey)
        self.connect('shutdown', self.on_shutdown)

        quit_action = Gio.SimpleAction.new('quit', None)
        quit_action.connect('activate', lambda *_: self.quit())
        self.add_action(quit_action)

        hotkey_action = Gio.SimpleAction.new('hotkey', GLib.VariantType('(sss)'))
        hotkey_action.connect('activate', self._on_hotkey_command)
        self.add_action(hotkey_action)

        # A session ending or a power button only sends these
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_unix_signal)

        # The config is written as it changes, a crash or a power cut can't take more with it
        GLib.timeout_add_seconds(AUTOSAVE_SECONDS, self._autosave)

    def _on_hotkey_command(self, _, parameter):
        if self.window is not None:
            self.hotkey_controller.run_action(*parameter.unpack())

    def _on_unix_signal(self):
        self.quit()
        return GLib.SOURCE_CONTINUE

    def _autosave(self):
        try:
            self.config_persistence.save_if_changed()
        except Exception:
            LOG.exception('Autosave failed')
        return GLib.SOURCE_CONTINUE

    def do_activate(self, *args, **kwargs):
        '''
        Called when the Application starts, and when it is started again while running.
        Args:
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        '''
        if self.window is not None:
            self.gtk_controller.show_window()
            return

        style.load()
        self.connect_registry_events()
        self.window = self.gtk_controller.create_window(self)
        self.gtk_controller.quit_on_close = self.quit_on_close
        self.connect_gtk_controller_events()
        self.connect_device_controller_events()
        self.connect_event_controller_events()

        self.app_registry.bootstrap()
        self.event_controller.start_listen()
        self.hotkey_controller.start()

        try:
            self.tray = TrayIcon(self.gtk_controller.toggle_window, self.quit)
        except GLib.Error as e:
            LOG.warning('No tray icon: %s', e.message)

        if not self.start_hidden:
            self.gtk_controller.show_window()

        # Greet new users on the very first launch (no config file present).
        if self.config_persistence.first_run and not self.start_hidden:
            self.gtk_controller.open_welcome_window()

    def on_shutdown(self, *_):
        '''
        Called when the Application quits (different from window destroy).
        Args:
            *_: Additional positional arguments.
        '''
        if self.window is None:
            return

        self.gtk_controller.save_window_state()
        self.hotkey_controller.stop()
        if self.config_model.cleanup is True:
            self.device_controller.cleanup()

        if self.tray is not None:
            self.tray.close()

        self.config_persistence.save()
        self.event_controller.stop_listen()

    def connect_registry_events(self):
        signal_map = {
            'entry_new': self.gtk_controller.app_entry_new,
            'entry_change': self.gtk_controller.app_entry_change,
            'entry_remove': self.gtk_controller.app_entry_remove,
        }

        for signal_name, callback in signal_map.items():
            self.app_registry.connect(signal_name, callback)

    def connect_gtk_controller_events(self):
        signal_map = {
            'volume': self.device_controller.set_volume,
            'mute': self.device_controller.set_mute,
            'primary': self.device_controller.set_primary,
            'hold_primary': self.device_controller.set_hold_primary,
            'connect': self.device_controller.set_connection,
            'route_volume': self.device_controller.set_route_volume,
            'use_loopback': self.device_controller.set_use_loopback,
            'connection_change': self.device_controller.update_connection,
            'device_new': self.device_controller.create_device,
            'device_remove': self.device_controller.remove_device,
            'device_change': self.device_controller.update_device,
            'eq_change': self.device_controller.set_eq,
            'pa_hotplug': self.device_controller.handle_hotplug,
            'pa_unplug': self.device_controller.handle_unplug,
            'app_volume': self.app_registry.set_volume,
            'app_mute': self.app_registry.set_mute,
            'app_device': self.app_registry.set_device,
            'app_forget': self.app_registry.forget,
            'app_add': self.app_registry.add_manually,
            'hotkeys_probe': self.hotkey_controller.probe,
            'hotkeys_apply': self.hotkey_controller.apply,
            'hotkeys_configure': self.hotkey_controller.configure,
            'quit': self.quit,
        }

        for signal_name, callback in signal_map.items():
            self.gtk_controller_handlers[signal_name] = self.gtk_controller.connect(signal_name, callback)

        self.hotkey_controller.connect('state_changed', self.gtk_controller.hotkeys_state_changed)

        # Streams may be waiting for a device that just came back, runs after handle_hotplug
        self.gtk_controller.connect('pa_hotplug', lambda *_: self.app_registry.device_appeared())

    def connect_device_controller_events(self):
        signal_map = {
            'device_new': self.gtk_controller.device_new_callback,
            'device_remove': self.gtk_controller.device_remove_callback,
            'device_change': self.gtk_controller.device_change_callback,
        }

        for signal_name, callback in signal_map.items():
            self.device_controller_handlers[signal_name] = self.device_controller.connect(signal_name, callback)
            # A hotkey's description has the device's name, and its hotkeys go with it
            self.device_controller.connect(signal_name, on_main_thread(lambda *_: self.hotkey_controller.sync()))

    def connect_event_controller_events(self):
        signal_map = {
            'pa_device_change': self.gtk_controller.pa_device_change_callback,
            'pa_device_new': self.gtk_controller.pa_device_new_callback,
            'pa_device_remove': self.gtk_controller.pa_device_remove_callback,
            'pa_primary_change': self.gtk_controller.pa_primary_change_callback,
            'pa_app_change': on_main_thread(self.app_registry.stream_change),
            'pa_app_new': on_main_thread(self.app_registry.stream_new),
            'pa_app_remove': on_main_thread(self.app_registry.stream_remove),
            'pa_client_new': on_main_thread(self.app_registry.client_new),
            'pa_client_remove': on_main_thread(self.app_registry.client_remove),
            'pa_server_lost': on_main_thread(self._on_server_lost),
            'pa_server_back': on_main_thread(self._on_server_back),
        }

        for signal_name, callback in signal_map.items():
            self.event_controller_handlers[signal_name] = self.event_controller.connect(signal_name, callback)

    def _on_server_lost(self):
        self.gtk_controller.set_server_lost(True)

    def _on_server_back(self):
        '''
        The sound server restarted, which is a reboot for everything NeoMixer put in it.
        '''
        pmctl.reconnect()
        self.device_controller.setup_graph()
        self.app_registry.bootstrap()
        self.gtk_controller.set_server_lost(False)
