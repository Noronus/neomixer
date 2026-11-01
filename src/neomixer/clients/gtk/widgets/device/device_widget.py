import gettext

from neomixer.model.device_model import DeviceModel

from neomixer.clients.gtk.widgets.common.volume_widget import VolumeWidget, format_db
from neomixer.clients.gtk.widgets.common.mute_widget import MuteWidget
from neomixer.clients.gtk.widgets.common.default_widget import DefaultWidget, HoldWidget
from neomixer.clients.gtk.widgets.common.vumeter_widget import VumeterWidget
from neomixer.clients.gtk.widgets.utils.widget_box import WidgetBox
from neomixer.clients.gtk.widgets.device.connection_widget import ConnectionWidget
from neomixer.clients.gtk.widgets.popovers.device_settings_popover import DeviceSettingsPopover

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GObject, Gtk, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

MIN_FADER_HEIGHT = 110
STRIP_WIDTH = 88


class DeviceWidget(Gtk.Box):
    '''
    A channel strip: name, fader, meter, buttons and, for inputs, its routes.
    '''

    __gsignals__ = {
        'mute': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        'primary': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        'hold_primary': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        'volume': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (int,)),
        'device_remove': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        'device_change': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        'connection': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str, bool)),
        'route_volume': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str, int)),
        'use_loopback': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str, bool)),
        'update_connection': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str, GObject.TYPE_PYOBJECT)),
        'settings_pressed': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        'connection_settings_pressed': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str, str)),
        'eq_pressed': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        'eq_toggled': (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, model: DeviceModel, routes_group: Gtk.SizeGroup = None, tag: str = ''):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=STRIP_WIDTH, hexpand=True)
        self.device_type = model.get_type()
        self.add_css_class('strip')
        self.add_css_class(f'strip-{self.device_type}')
        self.handlers = {}
        self.device_model = model
        self.routes_group = routes_group
        self._create_widgets(model, tag)
        self._arrange_widgets()
        self._connect_callbacks()
        self._update_warning()
        self.update_eq_state()

    def _create_widgets(self, model, tag):
        self.tag_label = Gtk.Label(label=tag, visible=bool(tag))
        self.tag_label.add_css_class('strip-tag')
        self.nick_label = Gtk.Label(
            label=model.nick, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=6
        )
        self.nick_label.add_css_class('strip-name')
        self.description_label = Gtk.Label(
            label=model.description, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=6
        )
        self.description_label.add_css_class('strip-description')
        self.description_label.set_tooltip_text(model.description)
        self.name_label = Gtk.Label(label=model.name)
        # Warning icon shown when device fails to initialize
        self.warning_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')

        self.volume_widget = VolumeWidget(
            value=model.volume[0], orientation=Gtk.Orientation.VERTICAL, inverted=True, vexpand=True,
            height_request=MIN_FADER_HEIGHT, halign=Gtk.Align.CENTER
        )
        self.value_label = Gtk.Label(label=format_db(model.volume[0]), halign=Gtk.Align.CENTER)
        self.value_label.add_css_class('strip-value')
        self.mute_widget = MuteWidget(active=model.mute, hexpand=True)
        self.vumeter_widget = VumeterWidget(vexpand=True)

        self.edit_button = Gtk.MenuButton(icon_name='view-more-symbolic', valign=Gtk.Align.CENTER)
        self.edit_button.add_css_class('icon-small')
        self.popover = DeviceSettingsPopover(model.get_type(), edit=True)
        self.edit_button.set_popover(self.popover)

        self.primary_widget = DefaultWidget(active=bool(model.primary), hexpand=True)
        self.hold_widget = HoldWidget(active=model.hold_primary, hexpand=True)

        self.eq_button = Gtk.Button(label=_('EQ'), hexpand=True)
        self.eq_button.add_css_class('strip-button')
        self.eq_button.add_css_class('eq-button')

        self.connections_widgets = {}
        if self.device_type in ('vi', 'hi'):
            self.connections_widgets = {
                'a': WidgetBox(orientation=Gtk.Orientation.VERTICAL, spacing=3),
                'b': WidgetBox(orientation=Gtk.Orientation.VERTICAL, spacing=3),
            }
            self._create_connection_widgets()

        Gtk.Accessible.update_property(self, [Gtk.AccessibleProperty.LABEL], [self.get_accessible_name()])

        self.edit_button.set_tooltip_text(_('Open device settings'))
        Gtk.Accessible.update_property(
            self.edit_button,
            [
                Gtk.AccessibleProperty.LABEL,
                Gtk.AccessibleProperty.DESCRIPTION,
                Gtk.AccessibleProperty.HAS_POPUP,
            ],
            [
                _('Device settings'),
                _('Open device settings'),
                True
            ]
        )

    def _arrange_widgets(self):
        bar = Gtk.Box()
        bar.add_css_class('strip-bar')
        self.append(bar)

        title = Gtk.Box(spacing=4)
        title.append(self.tag_label)
        title.append(self.warning_icon)
        title.append(self.nick_label)
        title.append(self.edit_button)
        self.append(title)
        self.append(self.description_label)

        # The fader carries the ticks, the meter sits right next to it
        fader_row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, vexpand=True, margin_top=6, margin_bottom=2)
        fader_row.append(self.vumeter_widget)
        fader_row.append(self.volume_widget)
        self.append(fader_row)
        self.append(self.value_label)

        # Not homogeneous: the widest of four buttons would decide how narrow a strip can get
        buttons = Gtk.Box(spacing=3, margin_top=4)
        buttons.append(self.mute_widget)
        buttons.append(self.eq_button)
        if self.device_model.primary is not None:
            buttons.append(self.primary_widget)
            buttons.append(self.hold_widget)
        self.append(buttons)

        # Same height on every strip, inputs fill it with routes: all faders line up
        self.routes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_top=6)
        if self.routes_group is not None:
            self.routes_group.add_widget(self.routes_box)
        if self.connections_widgets:
            self.routes_box.append(self.connections_widgets['a'])
            self.routes_box.append(self.connections_widgets['b'])
        else:
            self.sources_title = Gtk.Label(label=_('PLAYING HERE'), xalign=0)
            self.sources_title.add_css_class('strip-sources-title')
            self.sources_label = Gtk.Label(xalign=0, wrap=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=10, lines=6)
            self.sources_label.add_css_class('strip-sources')
            self.routes_box.append(self.sources_title)
            self.routes_box.append(self.sources_label)
        self.append(self.routes_box)

    def set_sources(self, nicks):
        '''Show which inputs are routed to this output.'''
        self.sources_title.set_visible(bool(nicks))
        self.sources_label.set_label('\n'.join(nicks))

    def get_accessible_name(self):
        accessible_name = self.device_model.nick
        if self.device_model.nick != self.device_model.description:
            accessible_name += f' {self.device_model.description}'

        return accessible_name

    def fill_settings(self):
        self.volume_widget.set_volume(self.device_model.volume[0])
        self.value_label.set_label(format_db(self.device_model.volume[0]))
        self.mute_widget.set_mute(self.device_model.mute)
        self.nick_label.set_label(self.device_model.nick)
        self.description_label.set_label(self.device_model.description)
        self.description_label.set_tooltip_text(self.device_model.description)
        self._update_warning()

    def set_tag(self, tag):
        self.tag_label.set_label(tag)
        self.tag_label.set_visible(bool(tag))

    def update_eq_state(self):
        '''Lit while the EQ is doing something, outlined when it is set up but switched off.'''
        eq = self.device_model.eq
        if eq.enabled:
            self.eq_button.add_css_class('eq-on')
        else:
            self.eq_button.remove_css_class('eq-on')

        if not eq.enabled and not eq.is_flat():
            self.eq_button.add_css_class('eq-curved')
        else:
            self.eq_button.remove_css_class('eq-curved')

        state = _('on') if eq.enabled else _('off')
        self.eq_button.set_tooltip_text(_('Equalizer (%s). Right click switches it on or off.') % state)

    def _update_warning(self):
        '''
        Two-state visual:
          - init_failed: warning triangle next to the name + tooltip with the error
          - not present (without init_failed): grey out the name labels +
            tooltip explaining the device is disconnected
          - otherwise: normal state
        '''
        failed = bool(getattr(self.device_model, 'init_failed', False))
        present = bool(getattr(self.device_model, 'present', True))
        self.warning_icon.set_visible(failed)

        if not present and not failed:
            self.add_css_class('disconnected')
        else:
            self.remove_css_class('disconnected')

        if failed:
            error = getattr(self.device_model, 'init_error', '') or _('Failed to create device')
            self.warning_icon.set_tooltip_text(error)
            self.nick_label.set_tooltip_text(error)
        elif not present:
            self.nick_label.set_tooltip_text(_('Device disconnected: %s') % self.device_model.name)
        else:
            self.nick_label.set_tooltip_text(self.device_model.nick)

    def refresh_warnings(self):
        '''Refresh own and child route warning indicators from current model state.'''
        self._update_warning()
        for output_type, widget_box in self.connections_widgets.items():
            for output_id, connection_widget in widget_box.widgets.items():
                connection_widget.update_warning()
                connection_widget.popover.refresh_status()

                # an open popover's grid still shows the ports from when it opened
                if connection_widget.popover.get_visible():
                    self.emit('connection_settings_pressed', output_type, output_id)

    def reload_connection_widgets(self):
        for widget_box in self.connections_widgets.values():
            for connection_widget in widget_box.widgets.values():
                connection_widget.popover.unparent()
            widget_box.clear()
        self._create_connection_widgets()

    def _create_connection_widgets(self):
        for output_type in ('a', 'b'):
            connections = self.device_model.connections[output_type]
            for position, output_id in enumerate(sorted(connections, key=int)):
                connection_schema = connections[output_id]
                tag = f'{output_type.upper()}{position + 1}'
                button = ConnectionWidget(connection_schema.nick, connection_schema, tag=tag, output_type=output_type)
                self.connections_widgets[output_type].add_widget(output_id, button)
                button.connect('connection', self._on_connection_change, output_type, output_id)
                button.connect('route_volume', self._on_route_volume, output_type, output_id)
                button.connect('use_loopback', self._on_use_loopback, output_type, output_id)
                button.connect('settings_pressed', self._on_connection_edit_pressed, output_type, output_id)
                button.popover.connect('settings_changed', self._on_connection_settings_changed, output_type, output_id)

    def _connect_callbacks(self):
        self.volume_widget.connect('volume', self._on_volume_change)
        self.mute_widget.connect('mute', self._on_mute_change)
        self.primary_widget.connect('primary', self._on_primary_change)
        self.hold_widget.connect('hold', self._on_hold_change)
        self.popover.confirm_button.connect('clicked', self._on_settings_save)
        self.popover.remove_button.connect('clicked', self._on_device_remove)
        self.eq_button.connect('clicked', self._on_eq_pressed)

        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)
        gesture.connect('pressed', self._on_eq_right_click)
        self.eq_button.add_controller(gesture)

        gesture = Gtk.GestureClick.new()
        gesture.connect("pressed", self._on_edit_pressed)
        self.edit_button.add_controller(gesture)

    def _on_device_remove(self, _):
        self.emit('device_remove')

    def _on_volume_change(self, _, value):
        self.value_label.set_label(format_db(value))
        self.emit('volume', value)

    def _on_connection_change(self, _, state, output_type, output_id):
        self.emit('connection', output_type, output_id, state)

    def _on_route_volume(self, _, volume, output_type, output_id):
        self.emit('route_volume', output_type, output_id, volume)

    def _on_use_loopback(self, _, state, output_type, output_id):
        self.emit('use_loopback', output_type, output_id, state)

    def _on_mute_change(self, _, state):
        self.emit('mute', state)

    def _on_primary_change(self, _):
        self.emit('primary')

    def _on_hold_change(self, _, state):
        self.emit('hold_primary', state)

    def _on_eq_pressed(self, _):
        self.emit('eq_pressed')

    def _on_eq_right_click(self, *_):
        self.emit('eq_toggled')

    def _on_connection_settings_changed(self, popover, schema, output_type, output_id):
        connection_widget = self.connections_widgets[output_type].widgets[output_id]
        auto_toggled = schema.auto_ports != connection_widget.connection_model.auto_ports
        self.emit('update_connection', output_type, output_id, schema)

        # the route was relinked with the new settings, so its warning may have changed
        connection_widget.update_warning()
        popover.refresh_status()

        # automatic and manual mapping tick different boxes, so the grid is rebuilt
        if auto_toggled:
            self.emit('connection_settings_pressed', output_type, output_id)

    def _on_settings_save(self, _):
        schema = self.popover.to_schema()
        if len(schema['nick'].strip()) == 0:
            return
        if self.popover.device_type in ('vi', 'b') and len(schema['name'].strip()) == 0:
            return
        if self.popover.device_type in ('a', 'hi') and schema['channels'] == 0:
            return
        self.emit('device_change', schema)

    def _on_edit_pressed(self, *_):
        self.emit('settings_pressed', self.popover)

    def _on_connection_edit_pressed(self, _, output_type, output_id):
        self.emit('connection_settings_pressed', output_type, output_id)

    def pa_device_change(self):
        volume = self.device_model.volume[0]
        mute = self.device_model.mute

        if self.volume_widget.get_volume() != volume:
            self.volume_widget.set_volume(volume)
            self.value_label.set_label(format_db(self.volume_widget.get_volume()))

        if self.mute_widget.get_mute() != mute:
            self.mute_widget.set_mute(mute)
