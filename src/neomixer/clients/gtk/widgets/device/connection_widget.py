import gettext

from neomixer.clients.gtk.widgets.popovers.edit_connection_widget import ConnectionSettingsPopup

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext


class ConnectionWidget(Gtk.Box):
    '''
    A route button, named after the output it plays into. Right click opens the route's
    settings, which is also where its volume is.
    '''

    __gsignals__ = {
        'connection': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        'route_volume': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (int,)),
        'use_loopback': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        'settings_pressed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, label: str, connection_model, tag: str = '', output_type: str = 'a', **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self.set_hexpand(True)
        self.connection_model = connection_model

        # Warning icon shown when route fails to set up
        self.warning_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')

        # Info icon shown when automatic mapping paired channels by order
        self.info_icon = Gtk.Image.new_from_icon_name('dialog-information-symbolic')

        tag_label = Gtk.Label(label=tag)
        tag_label.add_css_class('route-tag')
        name_label = Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END, hexpand=True, xalign=0, max_width_chars=8)

        toggle_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        toggle_content.append(tag_label)
        toggle_content.append(self.warning_icon)
        toggle_content.append(self.info_icon)
        toggle_content.append(name_label)
        self.toggle_button = Gtk.ToggleButton(active=connection_model.state, hexpand=True)
        self.toggle_button.set_child(toggle_content)
        self.toggle_button.add_css_class('route')
        self.toggle_button.add_css_class(f'route-{output_type}')
        self._toggle_handler_id = self.toggle_button.connect('toggled', self._on_toggled)

        # Right-click gesture for connection settings popup
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)
        gesture.connect("pressed", self._on_pressed)
        self.popover = ConnectionSettingsPopup(connection_model)
        self.popover.set_parent(self.toggle_button)
        self.toggle_button.add_controller(gesture)

        self.popover.connect('use_loopback', self._on_loopback_toggled)
        self.popover.connect('route_volume', self._on_volume_changed)

        self.append(self.toggle_button)

        # set_accessible() must run before update_warning() because it sets a default
        # tooltip on the toggle button - update_warning then overrides that tooltip with
        # the error message when failed.
        self.set_accessible()
        self.update_warning()

    def set_accessible(self):
        description_label = _('Route to %s. Right click for its settings and volume.') % self.connection_model.nick
        self.toggle_button.set_tooltip_text(description_label)
        Gtk.Accessible.update_property(self.toggle_button, [Gtk.AccessibleProperty.DESCRIPTION], [description_label])
        Gtk.Accessible.update_property(self.toggle_button, [Gtk.AccessibleProperty.HAS_POPUP], [True])

    def update_warning(self):
        """
        Show warning when this route has a real failure, or links only part of its channels.
        Show info when automatic mapping had to pair channels by order.
        """
        problem = self.connection_model.error_text() or self.connection_model.warning_text()
        order = self.connection_model.order_text()
        self.warning_icon.set_visible(bool(problem))
        self.info_icon.set_visible(not problem and bool(order))
        if problem or order:
            self.toggle_button.set_tooltip_text(problem or order)
        else:
            self.set_accessible()  # Restores normal tooltip

    def _on_toggled(self, widget):
        self.emit('connection', widget.get_active())
        self.update_warning()

    def _on_loopback_toggled(self, _popover, state):
        self.emit('use_loopback', state)
        self.update_warning()

    def _on_volume_changed(self, _popover, value):
        self.emit('route_volume', value)

    def _on_pressed(self, _, __, ___, ____):
        self.popover.popup()
        self.emit('settings_pressed')

    def set_connection(self, state):
        self.toggle_button.handler_block(self._toggle_handler_id)
        self.toggle_button.set_active(state)
        self.toggle_button.handler_unblock(self._toggle_handler_id)
