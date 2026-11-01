import gettext

from neomixer.model.eq_model import (
    GAINLESS_TYPES, MAX_FREQ, MAX_GAIN, MAX_PREAMP, MAX_Q, MIN_FREQ, MIN_Q, PRESETS
)
from neomixer.clients.gtk.widgets.eq.eq_curve import EqCurve, format_freq

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

FILTER_TYPES = (
    ('peak', _('Peak')),
    ('lowshelf', _('Low shelf')),
    ('highshelf', _('High shelf')),
    ('lowpass', _('Low pass')),
    ('highpass', _('High pass')),
    ('notch', _('Notch')),
)
CUSTOM_PRESET = _('Custom')


def _spin(low, high, step, digits, width=7):
    spin = Gtk.SpinButton.new_with_range(low, high, step)
    spin.set_digits(digits)
    spin.set_width_chars(width)
    spin.set_numeric(True)
    return spin


class EqWindow(Gtk.Window):
    '''
    Equalizer of one input device. It stays open next to the mixer, and every change applies
    right away through the "changed" signal.
    '''

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, device_model, **kwargs):
        super().__init__(title=_('EQ: %s') % device_model.nick, default_width=860, default_height=560, **kwargs)
        self.add_css_class('neomixer-eq')
        self.device_model = device_model
        self.eq = device_model.eq
        self.selected = 0
        self._syncing = False

        self.curve = EqCurve(self.eq)
        self.curve.connect('band_selected', self._on_curve_selected)
        self.curve.connect('changed', self._on_curve_changed)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(self._build_toolbar())
        main.append(self.curve)
        main.append(self._build_band_chips())
        main.append(self._build_band_details())
        self.set_child(main)

        self.refresh()

    def set_text_factor(self, factor):
        self.curve.set_text_factor(factor)

    def _build_toolbar(self):
        toolbar = Gtk.Box(spacing=10)
        toolbar.add_css_class('eq-toolbar')

        self.enable_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.enable_switch.set_tooltip_text(_('Switching off passes the audio through unchanged, for comparing'))
        self.enable_switch.connect('notify::active', self._on_enable)
        enable_label = Gtk.Label(label=_('Equalizer'))
        enable_label.add_css_class('strip-name')
        toolbar.append(enable_label)
        toolbar.append(self.enable_switch)

        self.preset_names = [*PRESETS, CUSTOM_PRESET]
        self.preset_dropdown = Gtk.DropDown.new_from_strings(self.preset_names)
        self.preset_dropdown.set_tooltip_text(_('Preset'))
        self.preset_handler = self.preset_dropdown.connect('notify::selected', self._on_preset)
        toolbar.append(self.preset_dropdown)

        self.reset_button = Gtk.Button(label=_('Reset'))
        self.reset_button.connect('clicked', self._on_reset)
        toolbar.append(self.reset_button)

        spacer = Gtk.Box(hexpand=True)
        toolbar.append(spacer)

        preamp_label = Gtk.Label(label=_('Preamp'))
        preamp_label.add_css_class('eq-label')
        toolbar.append(preamp_label)
        self.preamp_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -MAX_PREAMP, MAX_PREAMP, 0.5)
        self.preamp_scale.add_css_class('fader')
        self.preamp_scale.set_size_request(180, -1)
        self.preamp_scale.add_mark(0, Gtk.PositionType.TOP, None)
        self.preamp_scale.set_tooltip_text(_('Lower this when boosting, to keep room before clipping'))
        self.preamp_scale.connect('value-changed', self._on_preamp)
        toolbar.append(self.preamp_scale)
        self.preamp_value = Gtk.Label(xalign=1)
        self.preamp_value.add_css_class('eq-value')
        toolbar.append(self.preamp_value)
        return toolbar

    def _build_band_chips(self):
        chips = Gtk.Box(spacing=4, homogeneous=True)
        chips.add_css_class('eq-bands')
        self.chips = []
        group = None
        for index in range(len(self.eq.bands)):
            chip = Gtk.ToggleButton(group=group)
            chip.add_css_class('eq-chip')
            chip.connect('toggled', self._on_chip, index)
            group = group or chip
            self.chips.append(chip)
            chips.append(chip)
        return chips

    def _build_band_details(self):
        details = Gtk.Box(spacing=10)
        details.add_css_class('eq-detail')

        self.band_enabled = Gtk.CheckButton(label=_('Active'))
        self.band_enabled.connect('toggled', self._on_band_edit)
        details.append(self.band_enabled)

        self.type_dropdown = Gtk.DropDown.new_from_strings([name for _key, name in FILTER_TYPES])
        self.type_dropdown.connect('notify::selected', self._on_band_edit)
        details.append(self.type_dropdown)

        self.freq_spin = _spin(MIN_FREQ, MAX_FREQ, 1, 0)
        self.gain_spin = _spin(-MAX_GAIN, MAX_GAIN, 0.5, 1)
        self.q_spin = _spin(MIN_Q, MAX_Q, 0.05, 2)
        for label, spin in ((_('Hz'), self.freq_spin), (_('dB'), self.gain_spin), (_('Q'), self.q_spin)):
            spin.connect('value-changed', self._on_band_edit)
            caption = Gtk.Label(label=label)
            caption.add_css_class('eq-label')
            details.append(spin)
            details.append(caption)
        return details

    #
    # # Showing the model
    #
    def refresh(self):
        self._syncing = True
        eq = self.eq
        self.enable_switch.set_active(eq.enabled)
        self.preamp_scale.set_value(eq.preamp)
        self.preamp_value.set_label(f'{eq.preamp:+.1f} dB')
        preset = eq.preset if eq.preset in PRESETS else CUSTOM_PRESET
        self.preset_dropdown.set_selected(self.preset_names.index(preset))

        for index, chip in enumerate(self.chips):
            band = eq.bands[index]
            text = f'{index + 1} · {format_freq(band.freq)}'
            chip.set_label(text)
            if band.enabled:
                chip.remove_css_class('eq-chip-off')
            else:
                chip.add_css_class('eq-chip-off')
            if index == self.selected:
                chip.set_active(True)

        band = eq.bands[self.selected]
        self.band_enabled.set_active(band.enabled)
        self.type_dropdown.set_selected([key for key, _name in FILTER_TYPES].index(band.type))
        self.freq_spin.set_value(band.freq)
        self.gain_spin.set_value(band.gain)
        self.q_spin.set_value(band.q)
        self.gain_spin.set_sensitive(band.type not in GAINLESS_TYPES)
        self._syncing = False
        self.curve.queue_draw()

    def _edited(self):
        self.refresh()
        self.emit('changed')

    #
    # # User changes
    #
    def _on_enable(self, switch, _pspec):
        if self._syncing:
            return
        self.eq.enabled = switch.get_active()
        self._edited()

    def _on_preset(self, dropdown, _pspec):
        if self._syncing:
            return
        name = self.preset_names[dropdown.get_selected()]
        if name in PRESETS:
            self.eq.apply_preset(name)
            self._edited()

    def _on_reset(self, _):
        self.eq.reset()
        self._edited()

    def _on_preamp(self, scale):
        if self._syncing:
            return
        self.eq.preamp = round(scale.get_value(), 1)
        self.eq.preset = ''
        self._edited()

    def _on_chip(self, chip, index):
        if self._syncing or not chip.get_active():
            return
        self.selected = index
        self.curve.select(index)
        self.refresh()

    def _on_curve_selected(self, _curve, index):
        self.selected = index
        self.refresh()

    def _on_curve_changed(self, _curve):
        self._edited()

    def _on_band_edit(self, *_):
        if self._syncing:
            return
        band = self.eq.bands[self.selected]
        band.enabled = self.band_enabled.get_active()
        band.type = FILTER_TYPES[self.type_dropdown.get_selected()][0]
        band.freq = self.freq_spin.get_value()
        band.gain = 0.0 if band.type in GAINLESS_TYPES else self.gain_spin.get_value()
        band.q = self.q_spin.get_value()
        self.eq.preset = ''
        self._edited()
