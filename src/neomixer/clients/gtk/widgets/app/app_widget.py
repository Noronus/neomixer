import gettext
import logging

from neomixer.clients.gtk.widgets.common.volume_widget import VolumeWidget
from neomixer.clients.gtk.widgets.common.vumeter_widget import VumeterWidget
from neomixer.clients.gtk.widgets.common.mute_widget import MuteWidget
from neomixer.clients.gtk.widgets.app.app_dropdown import AppDropDown
from neomixer.clients.gtk.widgets.app.app_icons import resolve_icon

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext


LOG = logging.getLogger("generic")

STATE_TEXT = {
    'active': _('playing'),
    'idle': _('connected, silent'),
    'offline': _('not running'),
}
RECORDING_STATE_TEXT = {
    'active': _('recording'),
    'idle': _('connected, silent'),
    'offline': _('not running'),
}
STATES = ('active', 'idle', 'offline')


class AppWidget(Gtk.Box):
    '''
    A program in the app list, whether it is playing right now or not. A fixed compact size:
    many of them flow into a grid instead of taking the mixer's space.
    '''

    __gsignals__ = {
        "app_mute": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        "app_volume": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (int,)),
        "app_device": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (str,)),
        "app_forget": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, entry):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.entry = entry
        self.app_type = entry.app_type
        self.add_css_class('app-card')

        self.icon = Gtk.Image(pixel_size=28, valign=Gtk.Align.CENTER)
        self.icon.add_css_class('app-icon')
        self.label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=8)
        self.label.add_css_class('app-name')
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.START)
        self.dot.add_css_class('app-dot')
        self.status_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=8, hexpand=True)
        self.status_label.add_css_class('app-status')

        self.combobox = AppDropDown(entry.app_type)
        self.combobox.set_size_request(118, -1)
        self.combobox.set_valign(Gtk.Align.CENTER)
        self.forget_button = Gtk.Button(icon_name='window-close-symbolic', valign=Gtk.Align.CENTER)
        self.forget_button.add_css_class('icon-small')
        self.forget_button.set_tooltip_text(_('Forget this program and its settings'))

        self.volume_widget = VolumeWidget(value=entry.volume, orientation=Gtk.Orientation.HORIZONTAL, hexpand=True)
        self.value_label = Gtk.Label(label=f'{entry.volume}', xalign=1)
        self.value_label.add_css_class('app-value')
        self.mute_widget = MuteWidget(active=entry.mute, valign=Gtk.Align.CENTER)
        self.mute_widget.set_size_request(24, -1)

        # only playback apps get a meter: pulse meters a playback stream itself,
        # but a recording stream's level could only be guessed from its source
        self.vumeter_widget = None
        if self.app_type == 'sink_input':
            self.vumeter_widget = VumeterWidget(
                orientation=Gtk.Orientation.HORIZONTAL, thickness=4, hexpand=True
            )

        status_row = Gtk.Box(spacing=5)
        status_row.append(self.dot)
        status_row.append(self.status_label)

        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        names.append(self.label)
        names.append(status_row)

        top = Gtk.Box(spacing=6)
        top.append(self.icon)
        top.append(names)
        top.append(self.combobox)
        top.append(self.forget_button)
        self.append(top)

        controls = Gtk.Box(spacing=6)
        controls.append(self.volume_widget)
        controls.append(self.value_label)
        controls.append(self.mute_widget)
        self.append(controls)
        if self.vumeter_widget is not None:
            self.append(self.vumeter_widget)

        self.volume_widget.connect('volume', self._on_volume_change)
        self.mute_widget.connect('mute', self._on_mute_change)
        self.forget_button.connect('clicked', self._on_forget)
        self.shown_device = ''
        self._icon_source = None
        self.device_handler = self.combobox.connect('changed', self._on_device_change)

        self.refresh()

    def _on_volume_change(self, _, value):
        self.value_label.set_label(f'{int(value)}')
        self.emit('app_volume', int(value))

    def _on_mute_change(self, _, state):
        self.emit('app_mute', state)

    def _on_device_change(self, app_combobox):
        # A selection the program was given is not a choice. GTK can deliver that notification
        # after handlers were unblocked again, so it is told apart by what was set.
        chosen = app_combobox.get_active_text() or ''
        if chosen != self.shown_device:
            self.shown_device = chosen
            self.emit('app_device', chosen)

    def _on_forget(self, _):
        self.emit('app_forget')

    def refresh(self):
        '''Show the current state of the program.'''
        entry = self.entry
        self.label.set_label(entry.label)
        self.label.set_tooltip_text(self._name_tooltip())
        icon_source = (entry.icon, entry.key, entry.binary)
        if icon_source != self._icon_source:
            self._icon_source = icon_source
            self.icon.set_from_gicon(resolve_icon(*icon_source))

        state = entry.state
        for other in STATES:
            if other == state:
                self.add_css_class(f'app-{other}')
            else:
                self.remove_css_class(f'app-{other}')
        if entry.known.device:
            self.add_css_class('app-routed')
        else:
            self.remove_css_class('app-routed')

        texts = STATE_TEXT if self.app_type == 'sink_input' else RECORDING_STATE_TEXT
        text = texts[state]
        if len(entry.streams) > 1:
            text += f' · {len(entry.streams)}'
        self.status_label.set_label(text)
        self.forget_button.set_visible(state == 'offline')

        if self.volume_widget.get_volume() != entry.volume:
            self.volume_widget.set_volume(entry.volume)
            self.value_label.set_label(f'{int(self.volume_widget.get_volume())}')
        if self.mute_widget.get_mute() != entry.mute:
            self.mute_widget.set_mute(entry.mute)
        self.sync_device()

        movable = entry.movable
        self.combobox.set_sensitive(movable)
        if not movable:
            self.combobox.set_tooltip_text(_("This app doesn't allow changing its device"))

    def _name_tooltip(self):
        lines = [self.entry.label]
        titles = [stream.media_name for stream in self.entry.streams.values() if stream.media_name]
        lines += [f'• {title}' for title in titles]
        return '\n'.join(lines)

    def sync_device(self):
        '''Have the selector show where the program goes, without that counting as a choice.'''
        device = self.entry.device
        self.shown_device = device if self.combobox.has_device(device) else ''
        if self.combobox.get_active_text() != self.shown_device:
            self.combobox.set_active_device(device)
