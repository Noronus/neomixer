import gettext

from neomixer.clients.gtk.text_size import MIN_POINTS, MAX_POINTS

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext


def _option(label, tooltip):
    '''A setting's row, returns (row, switch).'''
    row = Gtk.Box(spacing=12)
    row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
    switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    row.append(switch)
    row.set_tooltip_text(tooltip)
    return row, switch


class SettingsMenuBox(Gtk.Box):
    '''
    App settings. They apply as soon as they are switched.
    '''

    __gsignals__ = {
        "settings_change": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        "help_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
        "quit_pressed": (GObject.SIGNAL_RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        self._filling = False

        autostart_row, self.autostart = _option(
            _('Start at login'), _('Start NeoMixer with your desktop session, so routing and EQ are there right away')
        )
        hidden_row, self.start_hidden = _option(
            _('Start hidden at login'), _('Start in the background without opening the window')
        )
        background_row, self.background = _option(
            _('Keep running when closed'),
            _('Closing the window keeps NeoMixer in the background: new app streams only go to '
              'their remembered output while it runs')
        )
        vumeters_row, self.vumeters = _option(_('VU meters'), _('Enable or disable VU meters (volume peak)'))
        cleanup_row, self.cleanup = _option(
            _('Remove devices on quit'), _('Enable or disable cleaning up devices and connections upon closing')
        )

        self.text_size = Gtk.SpinButton.new_with_range(MIN_POINTS, MAX_POINTS, 0.5)
        self.text_size.set_digits(1)
        self.text_size.set_numeric(True)
        self.text_size.set_valign(Gtk.Align.CENTER)
        text_size_row = Gtk.Box(spacing=12)
        text_size_row.append(Gtk.Label(label=_('Text size (pt)'), xalign=0, hexpand=True))
        text_size_row.append(self.text_size)
        text_size_row.set_tooltip_text(
            _('The same physical size on every monitor, whatever its resolution, as far as the monitor reports its size')
        )

        for row in (text_size_row, autostart_row, hidden_row, background_row, vumeters_row, cleanup_row):
            self.append(row)

        self.switches = (self.autostart, self.start_hidden, self.background, self.vumeters, self.cleanup)
        for switch in self.switches:
            switch.connect('notify::active', self._on_switched)
        self.text_size.connect('value-changed', self._on_switched)

        self.help_button = Gtk.Button(label=_('Guide'))
        self.help_button.set_tooltip_text(_('Open the welcome guide'))
        self.quit_button = Gtk.Button(label=_('Quit NeoMixer'), hexpand=True)
        self.quit_button.add_css_class('quit-button')
        self.quit_button.set_tooltip_text(_('Stop running in the background. Audio keeps its routes.'))

        button_box = Gtk.Box(spacing=6, margin_top=4)
        button_box.append(self.help_button)
        button_box.append(self.quit_button)
        self.append(button_box)

        self.help_button.connect('clicked', lambda _: self.emit('help_pressed'))
        self.quit_button.connect('clicked', lambda _: self.emit('quit_pressed'))

    def fill_settings(self, config_model, autostart_enabled, start_hidden):
        self._filling = True
        self.text_size.set_value(config_model.text_size)
        self.vumeters.set_active(config_model.vumeters)
        self.cleanup.set_active(config_model.cleanup)
        self.background.set_active(config_model.background)
        self.autostart.set_active(autostart_enabled)
        self.start_hidden.set_active(start_hidden)
        self.start_hidden.set_sensitive(autostart_enabled)
        self._filling = False

    def to_schema(self):
        return {
            'text_size': self.text_size.get_value(),
            'vumeters': self.vumeters.get_active(),
            'cleanup': self.cleanup.get_active(),
            'background': self.background.get_active(),
            'autostart': self.autostart.get_active(),
            'start_hidden': self.start_hidden.get_active(),
        }

    def _on_switched(self, *_):
        if self._filling:
            return
        self.start_hidden.set_sensitive(self.autostart.get_active())
        self.emit('settings_change', self.to_schema())
