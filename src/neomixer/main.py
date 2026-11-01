'''
Entry point
'''
import sys
import logging

from neomixer.settings import APP_ID, CONFIG_FILE
from neomixer.model.config_model import ConfigModel
from neomixer.clients.gtk.gtk_client import GtkClient
from neomixer.clients.cli.cli_client import parse_args
from neomixer.utils.config_persistence import ConfigPersistence
from neomixer.utils.desktop_integration import install_launcher
from neomixer.repository.device_repository import DeviceRepository
from neomixer.controller.device_controller import DeviceController

# pylint: disable=wrong-import-order,wrong-import-position
from gi.repository import Gio, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")


def start_gui(hidden=False):
    install_launcher()
    app = GtkClient(start_hidden=hidden)
    return app.run(None)


def quit_running():
    '''
    Ask the running instance to quit. A plain Gio.Application: if none is running this one
    becomes the first, which must not start the mixer only to quit it.
    '''
    app = Gio.Application(application_id=APP_ID)
    app.register(None)
    if app.get_is_remote():
        app.activate_action('quit', None)
        app.get_dbus_connection().flush_sync(None)


def hotkey(device_type, device_id, action):
    '''
    Do what a hotkey does, in the running instance: for a desktop without global shortcuts for
    programs, where this is what its own keys can run.
    '''
    app = Gio.Application(application_id=APP_ID)
    app.register(None)
    if not app.get_is_remote():
        print('NeoMixer is not running', file=sys.stderr)
        return 1

    app.activate_action('hotkey', GLib.Variant('(sss)', (device_type, device_id, action)))
    app.get_dbus_connection().flush_sync(None)
    return 0


def init_devices():
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    DeviceController(device_repository=device_repository)


def cleanup_devices():
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    device_controller = DeviceController(device_repository=device_repository)
    device_controller.cleanup()


def mute(device_type, device_id, state):
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    device_controller = DeviceController(device_repository=device_repository)
    device_controller.set_mute(device_type, device_id, state)


def volume(device_type, device_id, value):
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    device_controller = DeviceController(device_repository=device_repository)
    device_controller.set_volume(device_type, device_id, value)


def primary(device_type, device_id):
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    device_controller = DeviceController(device_repository=device_repository)
    device_controller.set_primary(device_type, device_id)


def connect(input_type, input_id, output_type, output_id, state):
    config_persistence = ConfigPersistence(ConfigModel, CONFIG_FILE)
    device_repository = DeviceRepository(config_persistence)
    device_controller = DeviceController(device_repository=device_repository)
    device_controller.set_connection(input_type, input_id, output_type, output_id, state)


def main():

    arg_map = {
        'quit': quit_running,
        'hotkey': hotkey,
        'init': init_devices,
        'cleanup': cleanup_devices,
        'mute': mute,
        'volume': volume,
        'primary': primary,
        'connect': connect
    }

    parsed = parse_args()
    command = parsed.command
    if command is None:
        return start_gui(parsed.hidden)

    args = [value for name, value in vars(parsed).items() if name not in ('command', 'hidden')]
    return arg_map[command](*args) or 0


if __name__ == '__main__':
    sys.exit(main())
