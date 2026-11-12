import logging
import asyncio
import gettext
import traceback
import threading

from concurrent.futures._base import CancelledError

import pulsectl

from neomixer.scripts import pmctl
from neomixer.scripts.pmctl_async import subscribe_peak
from neomixer.utils import autostart

from neomixer.model.signal_model import SignalModel
from neomixer.model.config_model import ConfigModel
from neomixer.model.device_model import DeviceModel

from neomixer.clients.gtk.osd import VolumeOsd
from neomixer.clients.gtk.text_size import TextScaler
from neomixer.clients.gtk.widgets.content import Content, SECTION_ORDER
from neomixer.clients.gtk.widgets.common.volume_widget import snap_to_unity
from neomixer.clients.gtk.widgets.device.device_widget import DeviceWidget
from neomixer.clients.gtk.widgets.app.app_widget import AppWidget
from neomixer.clients.gtk.widgets.app.app_dropdown import AppDropDown
from neomixer.clients.gtk.widgets.eq.eq_window import EqWindow
from neomixer.clients.gtk.widgets.welcome_window import WelcomeWindow
from neomixer.clients.gtk.widgets.hotkey_window import HotkeyWindow
from neomixer.settings import APP_ID

# pylint: disable=wrong-import-order,wrong-import-position
from gi import require_version as gi_require_version
gi_require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")

_ = gettext.gettext

# An app can open many streams, the meter only needs to show that it is playing
MAX_METERED_STREAMS = 4
EQ_APPLY_DELAY_MS = 30
# Holding a key stops here, 0 dB. Past it takes a press each, it should not get loud by accident.
HOTKEY_HOLD_CEILING = 100


class GtkController(SignalModel):
    '''
    GtkController emits signals for UI and application actions.

    Signals:
        volume(device_type: str, device_id: str, volume: int):
            Emitted when a device's volume is changed.
        mute(device_type: str, device_id: str, state: bool):
            Emitted when a device's mute state is changed.
        primary(device_type: str, device_id: str):
            Emitted when a device is set as primary.
        hold_primary(device_type: str, device_id: str, state: bool):
            Emitted when a device is held as primary, or no longer is.
        connect(input_type: str, input_id: str, output_type: str, output_id: str, state: bool):
            Emitted when a connection between devices is changed.
        connection_change(input_type: str, input_id: str, output_type: str, output_id: str, connection_model):
            Emitted when a connection model is updated.
        device_new(device_model):
            Emitted when a new device is created.
        device_remove(device_type: str, device_id: str):
            Emitted when a device is removed.
        device_change(schema, device_type: str, device_id: str):
            Emitted when a device's settings are updated.
        eq_change(device_type: str, device_id: str):
            Emitted when a device's EQ settings changed.
        app_volume(app_type: str, key: str, volume: int):
            Emitted when a program's volume is changed.
        app_mute(app_type: str, key: str, state: bool):
            Emitted when a program's mute state is changed.
        app_device(app_type: str, key: str, device_name: str):
            Emitted when a program's device is changed.
        app_forget(app_type: str, key: str):
            Emitted when a program should be removed from the list.
        app_add(app_type: str, key: str, label: str, icon, binary):
            Emitted when a program should be added to the list.
        hotkeys_probe():
            Emitted when it needs to be known if this desktop has global shortcuts.
        hotkeys_apply(edits: dict):
            Emitted when hotkeys were changed, {(device_type, device_id, action): keys or None}.
        hotkeys_configure():
            Emitted when the desktop's own settings for the hotkeys should open.
        quit():
            Emitted when the user asks to quit.
        pa_hotplug(device_type: str, device_id: str):
            Emitted on the GTK main thread when a configured device reappears
            in PulseAudio and needs the device controller to re-init/reconnect.
        pa_unplug(device_type: str, device_id: str):
            Emitted on the GTK main thread when a configured device disappears
            from PulseAudio and the device controller should mark it absent.
    '''

    window: Gtk.Window
    config_model: ConfigModel
    vumeter_loop: asyncio.AbstractEventLoop
    vumeter_thread: threading.Thread

    vumeter_tasks: dict[str, dict[str, asyncio.Task]]
    device_handlers: dict[str, dict[str, int]]

    def __init__(self, device_repository):

        super().__init__()

        self.device_repository = device_repository
        self.config_model = device_repository.config
        self.quit_on_close = False

        # create vumeter loop thread
        self.vumeter_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self.vumeter_thread = threading.Thread(target=self.vumeter_loop.run_forever, daemon=True)
        self.vumeter_thread.start()

        self.window = None
        self.content = None
        self.welcome_window = None
        self.hotkey_window = None
        self.osd = VolumeOsd()
        self.text_scaler = None
        self.status_label = None
        self.vumeter_tasks = {'a': {}, 'b': {}, 'vi': {}, 'hi': {}}
        self.app_meter_tasks = {}
        self.device_handlers = {'a': {}, 'b': {}, 'vi': {}, 'hi': {}}
        self.eq_windows = {}
        self.eq_apply_sources = {}
        self.server_lost = False

    #
    # # Window
    #
    def create_window(self, application):
        self.create_content()
        self.window = Gtk.Window(title='NeoMixer', application=application)
        self.window.add_css_class('neomixer')
        self.window.set_icon_name(APP_ID)
        self.window.set_titlebar(self._build_header())
        self.window.set_default_size(self.config_model.window_width, self.config_model.window_height)
        self.window.set_child(self.content)
        self.text_scaler = TextScaler(self.config_model.text_size)
        self.text_scaler.track(self.window, self.content.set_text_factor)
        if self.config_model.mixer_height <= 0:
            self.content.default_split(self.config_model.window_height)
        self.connect_window_gtk_events()

        self.load_app_combobox()
        self.load_device_list()
        return self.window

    def _build_header(self):
        header = Gtk.HeaderBar()
        logo = Gtk.Image(icon_name=APP_ID, pixel_size=20)
        neo = Gtk.Label(label='Neo')
        neo.add_css_class('brand-neo')
        mixer = Gtk.Label(label='Mixer')
        mixer.add_css_class('brand-name')
        self.status_label = Gtk.Label(label=_('Sound server is gone, waiting for it…'), visible=False, margin_start=10)
        self.status_label.add_css_class('server-lost')
        title = Gtk.Box()
        title.append(logo)
        logo.set_margin_end(6)
        title.append(neo)
        title.append(mixer)
        title.append(self.status_label)
        header.set_title_widget(title)
        header.pack_end(self.content.settings_button)
        return header

    def create_content(self):
        self.content = Content(
            apps_filter=self.config_model.apps_filter,
            show_offline=self.config_model.show_offline_apps,
            mixer_height=self.config_model.mixer_height,
        )
        self.connect_content_gtk_events()
        self._fill_settings()
        return self.content

    def show_window(self):
        was_visible = self.window.get_visible()
        self.window.present()
        if not was_visible:
            self.start_all_vumeters()

    def hide_window(self):
        self.save_window_state()
        self.window.set_visible(False)
        for eq_window in self.eq_windows.values():
            eq_window.set_visible(False)
        self.stop_all_vumeters()

    def toggle_window(self):
        if self.window.get_visible():
            self.hide_window()
        else:
            self.show_window()

    def save_window_state(self):
        width, height = self.window.get_default_size()
        self.config_model.window_width = width
        self.config_model.window_height = height
        self.config_model.mixer_height = self.content.paned.get_position()

    def on_close_request(self, _):
        '''
        Called when the main window gets closed
        '''
        self.save_window_state()
        if self.config_model.background and not self.quit_on_close:
            self.hide_window()
            return True
        return False

    def set_server_lost(self, lost):
        self.server_lost = lost
        self.status_label.set_visible(lost)
        self.stop_all_vumeters()
        if not lost and self.window.get_visible():
            self.start_all_vumeters()
        if not lost:
            self.refresh_all_warnings()

    #
    # # Device widgets
    #
    def _strip_tag(self, device_type, device_id):
        if device_type not in ('a', 'b'):
            return ''
        ids = sorted(self.device_repository.get_devices_by_type(device_type), key=int)
        return f'{device_type.upper()}{ids.index(device_id) + 1}' if device_id in ids else ''

    def _refresh_strip_tags(self):
        for device_type in ('a', 'b'):
            for device_id, widget in self.content.device_box[device_type].widgets.items():
                widget.set_tag(self._strip_tag(device_type, device_id))

    def create_device_widget(self, device_type, device_id, device_model, refresh=False):
        '''
        Insert a device widget and add it to a device box
            "device_type" is [vi, hi, a, b]
            "device" is the device widget to insert in the box
        '''
        device_widget = DeviceWidget(device_model, self.content.routes_group, self._strip_tag(device_type, device_id))
        self.content.device_box[device_type].add_widget(device_id, device_widget)
        self.connect_device_gtk_events(device_type, device_id, device_widget)
        self.content.relayout_sections()

        if refresh:
            self.reload_connection_widgets()
            self._refresh_strip_tags()
            self.load_app_combobox()

        return device_widget

    def remove_device_widget(self, device_type, device_id, refresh=False):
        '''
        Destroy a device widget and remove it from a device box
            "device_type" is [vi, hi, a, b]
            "device" is the device widget to remove from the box
        '''
        self._close_eq_window(device_type, device_id)
        device_widget = self.content.device_box[device_type].remove_widget(device_id)
        self.content.routes_group.remove_widget(device_widget.routes_box)
        # Removing is clicked in the strip's own popover. Disposed while open, GTK 4.8 keeps its
        # popup surface and the compositor's grab, and no other popover opens anymore.
        device_widget.popover.popdown()
        device_widget.edit_button.set_popover(None)
        device_widget.run_dispose()
        self.content.relayout_sections()

        if refresh:
            self.reload_connection_widgets()
            self._refresh_strip_tags()
            self.load_app_combobox()

        return device_widget

    def change_device_widget(self, device_type, device_id, device_model):
        device_widget = self.content.device_box[device_type].widgets[device_id]
        device_widget.fill_settings()
        self.reload_connection_widgets()
        self.load_app_combobox()

    def on_device_widget_destroy(self, _, device_type, device_id):
        self.stop_vumeter(device_type, device_id)
        self.device_handlers[device_type].pop(device_id, None)

    def reload_connection_widgets(self):
        '''
        Reloads all connection widgets
        '''
        for device_type in ('hi', 'vi'):
            for _, device in self.content.device_box[device_type].widgets.items():
                device.reload_connection_widgets()
        self.refresh_output_sources()

    def refresh_output_sources(self):
        '''Have each output show which inputs play into it.'''
        for output_type in ('a', 'b'):
            for output_id, widget in self.content.device_box[output_type].widgets.items():
                widget.set_sources([
                    input_device.nick
                    for input_type in ('hi', 'vi')
                    for input_device in self.device_repository.get_devices_by_type(input_type).values()
                    if input_device.connections[output_type].get(output_id) is not None
                    and input_device.connections[output_type][output_id].state
                ])

    def load_device_list(self):
        '''
        Load the devices from config
        '''
        for device_type, device_dict in self.device_repository.get_all_devices().items():
            for device_id, device_model in device_dict.items():
                self.create_device_widget(device_type, device_id, device_model)
        self.refresh_output_sources()

    #
    # # Programs
    #
    def app_entry_new(self, entry):
        card = AppWidget(entry)
        card.connect('app_volume', self.set_app_volume, entry.app_type, entry.key)
        card.connect('app_mute', self.set_app_mute, entry.app_type, entry.key)
        card.connect('app_device', self.set_app_device, entry.app_type, entry.key)
        card.connect('app_forget', self.forget_app, entry.app_type, entry.key)
        self.content.add_app_card(card)
        self._sync_app_meters(entry)

    def app_entry_change(self, entry):
        card = self.content.app_cards.get((entry.app_type, entry.key))
        if card is None:
            self.app_entry_new(entry)
            return

        was = card.entry.label, card.get_css_classes()
        card.refresh()
        if was != (card.entry.label, card.get_css_classes()):
            self.content.refresh_apps_view()
        self._sync_app_meters(entry)

    def app_entry_remove(self, app_type, key):
        self._stop_app_meters(app_type, key)
        self.content.remove_app_card(app_type, key)

    def load_app_combobox(self):
        '''
        (Re)fill what a program can be routed to, and have every program's selector show its
        device in the new list.
        '''
        for card in self.content.app_cards.values():
            card.combobox.handler_block(card.device_handler)

        sink_input_device_list = [(_('Default'), '')]
        source_output_device_list = [(_('Default'), '')]

        # apps play into a sink (vi/a)
        for device_type in ('vi', 'a'):
            for device in self.device_repository.get_devices_by_type(device_type).values():
                sink_input_device_list.append((device.nick, device.name))

        # apps record from a source: a vi's monitor, or a hi/b directly
        for device in self.device_repository.get_devices_by_type('vi').values():
            source_output_device_list.append((device.nick, device.name + '.monitor'))
        for device_type in ('hi', 'b'):
            for device in self.device_repository.get_devices_by_type(device_type).values():
                source_output_device_list.append((device.nick, device.name))

        AppDropDown.set_device_list('sink_input', sink_input_device_list)
        AppDropDown.set_device_list('source_output', source_output_device_list)

        for card in self.content.app_cards.values():
            card.sync_device()
            card.combobox.handler_unblock(card.device_handler)

    def forget_app(self, _, app_type, key):
        self.emit('app_forget', app_type, key)

    def on_app_picked(self, _, key, label, binary):
        self.emit('app_add', 'sink_input', key, label, None, binary or None)

    def on_apps_view_changed(self, _):
        self.config_model.apps_filter = self.content.apps_filter
        self.config_model.show_offline_apps = self.content.show_offline

    #
    # # Settings
    #
    def _fill_settings(self):
        self.content.settings_box.fill_settings(self.config_model, autostart.is_enabled(), autostart.starts_hidden())

    def settings_menu_open(self, _):
        self._fill_settings()

    def settings_menu_apply(self, _, config_schema):
        vumeters_changed = self.config_model.vumeters != config_schema['vumeters']
        self.config_model.vumeters = config_schema['vumeters']
        self.config_model.cleanup = config_schema['cleanup']
        self.config_model.background = config_schema['background']
        self.config_model.text_size = config_schema['text_size']
        self.text_scaler.set_points(config_schema['text_size'])
        autostart.set_enabled(config_schema['autostart'], config_schema['start_hidden'])
        if vumeters_changed:
            if self.config_model.vumeters:
                self.start_all_vumeters()
            else:
                self.stop_all_vumeters()

    def quit_pressed(self, _):
        self.emit('quit')

    #
    # # EQ
    #
    def open_eq_window(self, _, device_type, device_id):
        window = self.eq_windows.get((device_type, device_id))
        if window is None:
            device_model = self.device_repository.get_device(device_type, device_id)
            window = EqWindow(device_model, transient_for=self.window)
            window.set_hide_on_close(True)
            window.connect('changed', self._on_eq_window_changed, device_type, device_id)
            self.text_scaler.track(window, window.set_text_factor)
            self.eq_windows[(device_type, device_id)] = window
        window.present()

    def toggle_eq(self, _, device_type, device_id):
        device_model = self.device_repository.get_device(device_type, device_id)
        device_model.eq.enabled = not device_model.eq.enabled
        window = self.eq_windows.get((device_type, device_id))
        if window is not None:
            window.refresh()
        self._on_eq_window_changed(None, device_type, device_id)

    def _on_eq_window_changed(self, _, device_type, device_id):
        widget = self.content.device_box[device_type].widgets.get(device_id)
        if widget is not None:
            widget.update_eq_state()

        # A drag changes the EQ on every pointer event, that many updates would only pile up
        key = (device_type, device_id)
        if key not in self.eq_apply_sources:
            self.eq_apply_sources[key] = GLib.timeout_add(EQ_APPLY_DELAY_MS, self._apply_eq, device_type, device_id)

    def _apply_eq(self, device_type, device_id):
        self.eq_apply_sources.pop((device_type, device_id), None)
        try:
            self.emit('eq_change', device_type, device_id)
        except Exception:
            LOG.exception('Failed to apply the EQ of %s/%s', device_type, device_id)
        return GLib.SOURCE_REMOVE

    def _close_eq_window(self, device_type, device_id):
        window = self.eq_windows.pop((device_type, device_id), None)
        if window is not None:
            self.text_scaler.untrack(window)
            window.destroy()

    #
    # # Signal wiring
    #
    def connect_window_gtk_events(self):
        '''
        Connect window events to the model
        '''

        signal_map = {
            'close-request': self.on_close_request
        }

        for signal_name, callback in signal_map.items():
            self.window.connect(signal_name, callback)

    def connect_device_gtk_events(self, device_type: str, device_id: str, device: DeviceWidget):
        '''
        Connect a device widget events to the model
        '''

        signal_map = {
            'volume': self.set_volume,
            'mute': self.set_mute,
            'connection': self.set_connection,
            'route_volume': self.set_route_volume,
            'use_loopback': self.set_use_loopback,
            'primary': self.set_primary,
            'hold_primary': self.set_hold_primary,
            'device_change': self.update_device_model,
            'device_remove': self.device_remove,
            'update_connection': self.update_connection,
            'destroy': self.on_device_widget_destroy,
            'settings_pressed': self.add_device_hijack,
            'connection_settings_pressed': self.connection_settings_hijack,
            'eq_pressed': self.open_eq_window,
            'eq_toggled': self.toggle_eq,
        }

        device_handler = self.device_handlers[device_type][device_id] = {}
        for signal_name, callback in signal_map.items():
            device_handler[signal_name] = device.connect(signal_name, callback, device_type, device_id)

        self._start_device_vumeter(device_type, device_id)

        return device

    def connect_content_gtk_events(self):
        signal_map = {
            'add_device_pressed': self.add_device_hijack,
            'device_new': self.device_new,
            'settings_pressed': self.settings_menu_open,
            'settings_change': self.settings_menu_apply,
            'help_pressed': self.open_welcome_window,
            'hotkeys_pressed': self.open_hotkey_window,
            'quit_pressed': self.quit_pressed,
            'app_picked': self.on_app_picked,
            'apps_view_changed': self.on_apps_view_changed,
        }

        for signal_name, callback in signal_map.items():
            self.content.connect(signal_name, callback)

    def open_welcome_window(self, *_):
        '''
        Create and present the welcome window.
        '''
        self.welcome_window = WelcomeWindow(transient_for=self.window)
        self.text_scaler.track(self.welcome_window)
        self.welcome_window.present()

    #
    # # Hotkeys
    #
    def open_hotkey_window(self, *_):
        if self.hotkey_window is not None:
            self.hotkey_window.present()
            return

        self.hotkey_window = HotkeyWindow(self.config_model.volume_step, self.config_model.snap_db, transient_for=self.window)
        self.hotkey_window.connect('apply', lambda _, edits: self.emit('hotkeys_apply', edits))
        self.hotkey_window.connect('configure', lambda _: self.emit('hotkeys_configure'))
        self.hotkey_window.connect('step_changed', self._on_hotkey_step_changed)
        self.hotkey_window.connect('snap_changed', self._on_hotkey_snap_changed)
        self.hotkey_window.connect('osd_changed', self._on_hotkey_osd_changed)
        self.hotkey_window.connect('close-request', self._on_hotkey_window_close)
        self.hotkey_window.set_devices(self._hotkey_devices())
        self.text_scaler.track(self.hotkey_window)
        self.hotkey_window.present()
        self.emit('hotkeys_probe')

    def _on_hotkey_window_close(self, window):
        self.text_scaler.untrack(window)
        self.hotkey_window = None
        return False

    def _on_hotkey_step_changed(self, _, step):
        self.config_model.volume_step = step

    def _on_hotkey_snap_changed(self, _, snap_db):
        self.config_model.snap_db = snap_db

    def _on_hotkey_osd_changed(self, _, device_type, device_id, state):
        device = self.device_repository.get_device(device_type, device_id)
        if device is not None:
            device.hotkey_osd = state

    def _hotkey_devices(self):
        return [
            (device_type, device_id, device.nick, dict(device.hotkeys), device.hotkey_osd)
            for device_type in SECTION_ORDER
            for device_id, device in sorted(self.device_repository.get_devices_by_type(device_type).items(), key=lambda i: int(i[0]))
        ]

    def hotkeys_state_changed(self, controller):
        if self.hotkey_window is not None:
            self.hotkey_window.set_devices(self._hotkey_devices())
            self.hotkey_window.set_state(controller)

    def perform_hotkey(self, device_type, device_id, action, held):
        '''
        Do what a hotkey stands for, by way of the strip's own controls: it is the same as using them.
        Returns if a held key can go on.
        '''
        widget = self.content.device_box[device_type].widgets.get(device_id)
        if widget is None or not widget.device_model.present:
            return False

        if action == 'mute':
            widget.mute_widget.set_active(not widget.mute_widget.get_active())
            self._show_volume(widget)
            return False

        volume = widget.volume_widget.get_value()
        step = self.config_model.volume_step
        target = snap_to_unity(volume, volume + step if action == 'up' else volume - step, self.config_model.snap_db)
        if held and action == 'up':
            target = min(target, max(volume, HOTKEY_HOLD_CEILING))
        widget.volume_widget.set_value(target)
        # Also when it is at the end already: the key was pressed to know, too
        self._show_volume(widget)
        return widget.volume_widget.get_value() != volume

    def mixer_in_front(self):
        '''Is the mixer where its faders can be seen moving: shown, and what the keyboard is on.'''
        return self.window is not None and self.window.get_visible() and self.window.is_active()

    def _show_volume(self, widget):
        '''The volume on screen, for a device that has it switched on, see clients.gtk.osd.'''
        device = widget.device_model
        if device.hotkey_osd and not self.mixer_in_front():
            self.osd.show(device.nick, widget.volume_widget.get_value(), widget.mute_widget.get_active(),
                          device.device_type == 'source')

    def connection_settings_hijack(self, device_widget, output_type, output_id, input_type, input_id):
        '''
        Populates the device combobox every time the popup opens, so that the
        devices are always up to date
        '''
        input_model = self.device_repository.get_device(input_type, input_id)
        output_model = self.device_repository.get_device(output_type, output_id)
        button = device_widget.connections_widgets[output_type].get_widget(output_id)
        port_map_widget = button.popover.port_map_widget
        port_map_widget.clear_port_map()

        try:
            input_ports = pmctl.get_ports('output', input_model.name)
            output_ports = pmctl.get_ports('input', output_model.name)
            port_map_widget.create_routing_grid(input_model, output_model, input_ports, output_ports)
        except Exception:
            LOG.exception('Failed to build routing grid for %s -> %s', input_model.name, output_model.name)
            port_map_widget.clear_port_map()
            port_map_widget.show_unavailable()

        # after the grid, since the note under it is sized from the grid's width
        button.popover.fill_settings()

    def add_device_hijack(self, _, settings_widget, device_type, device_id):
        '''
        Populates the device combobox every time the popup opens, so that the
        devices are always up to date
        '''

        if device_type in ('a', 'hi'):
            device_list = self.list_devices(device_type)
            settings_widget.combobox_widget.empty()
            settings_widget.combobox_widget.load_list(device_list, 'description')

        if device_id:
            device_model = self.device_repository.get_device(device_type, device_id)
            settings_widget.fill_settings(device_model)

    def list_devices(self, device_type):
        '''
        List devices of a given type.
        Args:
            device_type (str): Type of device ('a' for sink, 'b' for source).
        Returns:
            list: List of DeviceModel objects.
        '''
        dvtp = 'sink' if device_type == 'a' else 'source'
        pa_device_list = pmctl.list_devices(dvtp)
        device_list = []
        for device in pa_device_list:
            device_model = DeviceModel.pa_to_device_model(device, dvtp)
            device_list.append(device_model)
        return device_list

    #
    # # VU meters
    #
    def handle_vumeter_error(self, fut):
        try:
            fut.result()

        except CancelledError:
            LOG.debug("VUmeter task canceled")

        except (pulsectl.PulseError, pulsectl.PulseDisconnected) as e:
            LOG.debug("VUmeter lost its stream: %r", e)

        except Exception as e:
            tb_str = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
            LOG.error("Vumeter task error: \n %s", tb_str)

    def start_vumeter(self, app_type, app_name, vumeter_widget, stream_index=None):
        future = asyncio.run_coroutine_threadsafe(
            subscribe_peak(app_name, app_type, vumeter_widget.update_peak, stream_index=stream_index),
            self.vumeter_loop
        )

        future.add_done_callback(self.handle_vumeter_error)
        return future

    def stop_vumeter(self, type_: str, id_: str):
        """
        Cancel a vumeter task and idle its bar. Safe to call when no
        task is running or when the widget has already been removed.
        """
        task = self.vumeter_tasks[type_].pop(id_, None)
        if task is not None:
            task.cancel()
        widget = self.content.device_box[type_].widgets.get(id_)
        if widget is not None and widget.vumeter_widget is not None:
            widget.vumeter_widget.reset()

    def stop_all_vumeters(self):
        for device_type in ('a', 'b', 'vi', 'hi'):
            for device_id in list(self.vumeter_tasks[device_type]):
                self.stop_vumeter(device_type, device_id)
        for key in list(self.app_meter_tasks):
            self._stop_app_meters(*key)

    def start_all_vumeters(self):
        for device_type in ('a', 'b', 'vi', 'hi'):
            for device_id in list(self.content.device_box[device_type].widgets):
                self._start_device_vumeter(device_type, device_id)
        for card in self.content.app_cards.values():
            self._sync_app_meters(card.entry)

    def _meters_wanted(self):
        return self.config_model.vumeters and not self.server_lost and self.window is not None and self.window.get_visible()

    def _start_device_vumeter(self, device_type: str, device_id: str):
        """Subscribe a peak stream for a device widget. No-op if vumeters are off or the widget is gone."""
        if device_id in self.vumeter_tasks[device_type]:
            return
        if not self.config_model.vumeters or self.server_lost or (self.window is not None and not self.window.get_visible()):
            return
        device_widget = self.content.device_box[device_type].widgets.get(device_id)
        if device_widget is None:
            return
        pa_device_type = device_widget.device_model.device_type
        self.vumeter_tasks[device_type][device_id] = self.start_vumeter(
            pa_device_type, device_widget.device_model.name, device_widget.vumeter_widget
        )

    def _sync_app_meters(self, entry):
        """Have a meter running per open stream of a program, up to a few."""
        key = (entry.app_type, entry.key)
        card = self.content.app_cards.get(key)
        if card is None or card.vumeter_widget is None:
            return

        wanted = list(entry.streams)[:MAX_METERED_STREAMS] if self._meters_wanted() else []
        tasks = self.app_meter_tasks.setdefault(key, {})
        for index in list(tasks):
            if index not in wanted:
                tasks.pop(index).cancel()

        # pipewire-pulse attaches the meter to the stream itself (by index),
        # so it follows the app across moves without a restart
        for index in wanted:
            if index not in tasks:
                tasks[index] = self.start_vumeter('sink', entry.streams[index].device, card.vumeter_widget, index)

        if not wanted:
            card.vumeter_widget.reset()

    def _stop_app_meters(self, app_type, key):
        for task in self.app_meter_tasks.pop((app_type, key), {}).values():
            task.cancel()
        card = self.content.app_cards.get((app_type, key))
        if card is not None and card.vumeter_widget is not None:
            card.vumeter_widget.reset()

    def _restart_device_vumeter(self, device_type: str, device_id: str):
        """(Re)subscribe a peak stream for a device that just appeared in PA."""
        self.stop_vumeter(device_type, device_id)
        self._start_device_vumeter(device_type, device_id)

    #
    # # Update model functions
    #
    def set_volume(self, _, volume: int, device_type, device_id):
        '''
        Set model volume
        '''
        self.emit('volume', device_type, device_id, volume)

    def set_mute(self, _, state: bool, device_type, device_id):
        '''
        Set model mute
        '''
        self.emit('mute', device_type, device_id, state)

    def set_primary(self, _, device_type, device_id):
        '''
        Set model primary
        '''
        self.emit('primary', device_type, device_id)
        self._show_primary(device_type)

    def set_hold_primary(self, _, state: bool, device_type, device_id):
        '''
        Hold a device as primary, or let it go
        '''
        self.emit('hold_primary', device_type, device_id, state)
        self._show_primary(device_type)

    def _show_primary(self, device_type):
        for device in self.content.device_box[device_type].widgets.values():
            device.primary_widget.set_primary(bool(device.device_model.primary))
            device.hold_widget.set_hold(device.device_model.hold_primary)

    def set_connection(self, _, output_type, output_id, state: bool, input_type, input_id):
        '''
        Call to device model to set model connection
        '''
        self.emit('connect', input_type, input_id, output_type, output_id, state)
        self.refresh_output_sources()

    def set_route_volume(self, _, output_type, output_id, volume: int, input_type, input_id):
        '''
        Relay per-route volume change to device controller
        '''
        self.emit('route_volume', input_type, input_id, output_type, output_id, volume)

    def set_use_loopback(self, _, output_type, output_id, state: bool, input_type, input_id):
        '''
        Relay use_loopback toggle to device controller
        '''
        self.emit('use_loopback', input_type, input_id, output_type, output_id, state)

    def update_connection(self, _, output_type, output_id, connection_model, input_type, input_id):
        '''
        Call to device model to set model connection
        '''
        self.emit('connection_change', input_type, input_id, output_type, output_id, connection_model)

    def _name_taken(self, schema, own=None):
        '''
        A virtual device is its name to the sound server. Another strip with the same name would
        be the same device, whatever its own settings say, and wait for ports it never gets.
        '''
        if schema['device_class'] != 'virtual':
            return False

        taken = self.device_repository.find_device_by_key('name', schema['name'], ('vi', 'b'))
        return any(device is not own for _, _, device in taken)

    def device_new(self, _, schema):
        '''
        Call to device model to create new device model
        '''
        is_sink = schema['device_type'] == 'sink'
        device_type = ('vi' if is_sink else 'b') if schema['device_class'] == 'virtual' else ('a' if is_sink else 'hi')
        popover = self.content.create_device_button[device_type].get_popover()
        if self._name_taken(schema):
            popover.show_name_taken()
            return

        self.emit('device_new', schema)

        # Left open it looks like the settings of what was just created, and it is not
        popover.clear()
        popover.popdown()

    def device_remove(self, _, device_type, device_id):
        '''
        Call device manager to remove device model
        '''
        self.emit('device_remove', device_type, device_id)

    def update_device_model(self, _, schema, device_type, device_id):
        '''
        Call to device model to update a device settings
        '''
        if self._name_taken(schema, self.device_repository.get_device(device_type, device_id)):
            self.content.device_box[device_type].widgets[device_id].popover.show_name_taken()
            return

        self.emit('device_change', schema, device_type, device_id)

    def set_app_volume(self, _, volume: int, app_type, key):
        '''
        Set a program's volume
        '''
        self.emit('app_volume', app_type, key, volume)

    def set_app_mute(self, _, state: bool, app_type, key):
        '''
        Set a program's mute
        '''
        self.emit('app_mute', app_type, key, state)

    def set_app_device(self, _, device_name: str, app_type, key):
        '''
        Set a program's device
        '''
        self.emit('app_device', app_type, key, device_name)

    #
    # # End model update functions
    #

    #
    # # Model Callback functions
    #
    def device_new_callback(self, device_type, device_id, device_model):
        def wrapper():
            self.create_device_widget(device_type, device_id, device_model, refresh=True)
            return False

        GLib.idle_add(wrapper)

    def device_remove_callback(self, device_type: str, device_id: str):
        def wrapper():
            self.remove_device_widget(device_type, device_id, refresh=True)
            return False

        GLib.idle_add(wrapper)

    def device_change_callback(self, device_type: str, device_id: str, device_model):
        def wrapper():
            self.change_device_widget(device_type, device_id, device_model)
            return False

        GLib.idle_add(wrapper)

    def pa_device_change_callback(self, device_type: str, device_id: str, device_model: DeviceModel):
        def wrapper():
            device_widget = self.content.device_box[device_type].widgets[device_id]
            device_widget.pa_device_change()
            return False

        GLib.idle_add(wrapper)

    def refresh_all_warnings(self):
        """
        Walk every device widget and re-sync its warning state (device + routes)
        from the current model. Called after a hot-plug because a single
        device appearing can resolve route failures across many input devices.
        """
        for device_type in self.content.device_box:
            for device_widget in self.content.device_box[device_type].widgets.values():
                device_widget.refresh_warnings()

    def pa_device_new_callback(self, device_type: str, device_id: str):
        self._dispatch_pa_device_event(device_type, device_id, 'pa_hotplug', "Hot-plug")

    def pa_device_remove_callback(self, device_type: str, device_id: str):
        self._dispatch_pa_device_event(device_type, device_id, 'pa_unplug', "Unplug")

    def _dispatch_pa_device_event(self, device_type: str, device_id: str, signal_name: str, kind: str):
        """
        Send a hot-plug/unplug event from the EventController's asyncio
        thread to the GTK main thread, run the device-controller logic via
        signal, then refresh warning indicators. Refresh runs even if the
        handler raises so the UI never gets stuck on a stale state.
        """
        is_unplug = signal_name == 'pa_unplug'

        def run_on_main():
            if is_unplug:
                self.stop_vumeter(device_type, device_id)
            try:
                self.emit(signal_name, device_type, device_id)
            except Exception:
                LOG.exception("%s handling failed for %s/%s", kind, device_type, device_id)
            if not is_unplug:
                self._restart_device_vumeter(device_type, device_id)
            self.refresh_all_warnings()
            return False

        GLib.idle_add(run_on_main)

    def pa_primary_change_callback(self, device_type: str, device_id: str):
        def wrapper():
            for target_id, target_device in self.content.device_box[device_type].widgets.items():
                target_device.primary_widget.set_primary(target_id == device_id)

            return False

        GLib.idle_add(wrapper)

    #
    # # End Model Callback functions
    #
