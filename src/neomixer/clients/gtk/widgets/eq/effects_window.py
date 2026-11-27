import gettext

from neomixer.model.effects_model import (
    PRESETS, CompressorModel, DeesserModel, GateModel, LimiterModel, NormalizeModel
)
from neomixer.clients.gtk.widgets.eq.eq_panel import EqPanel

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GObject  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext


def _format_freq(value):
    return f'{value / 1000:.1f} kHz'


def _format_ratio(value):
    return f'{value:.1f} : 1'


class _Param:
    def __init__(self, key, label, low, high, step, unit='dB', digits=0, formatter=None, tooltip=None):
        self.key, self.label, self.low, self.high, self.step = key, label, low, high, step
        self.formatter = formatter or (lambda value: f'{value:.{digits}f} {unit}')
        self.digits = digits
        self.tooltip = tooltip


# In the order the audio goes through them, see model.effects_model
EFFECTS = (
    ('gate', _('Noise gate'), GateModel, _(
        'Turns the audio down while it is quieter than the threshold, like the hiss and hum of a '
        'microphone between words. Set the threshold a little above the noise.'
    ), (
        _Param('threshold', _('Threshold'), -80, -10, 1),
    )),
    ('eq', _('Equalizer'), None, None, ()),
    ('normalize', _('Normalization'), NormalizeModel, _(
        'Keeps the loudness about the same: quiet scenes get turned up, loud ones down. It measures '
        'the last 3 seconds the way TV stations do (EBU R 128), so it is smooth, and too slow for a '
        'sudden bang: for films, the compressor and limiter catch those.'
    ), (
        _Param('target', _('Target'), -36, -6, 1, unit='LUFS',
               tooltip=_('How loud it keeps things. Lower leaves more room for loud moments.')),
        _Param('max_boost', _('Most boost'), 0, 20, 1,
               tooltip=_('How far quiet passages and silence may get turned up')),
        _Param('max_cut', _('Most cut'), 0, 40, 1),
    )),
    ('compressor', _('Compressor'), CompressorModel, _(
        'Turns loud moments down as they happen. Above the threshold, a ratio of 4 : 1 lets the '
        'level rise by only 1 dB for every 4 dB. Makeup turns everything up again afterwards.'
    ), (
        _Param('threshold', _('Threshold'), -60, 0, 1),
        _Param('ratio', _('Ratio'), 1, 20, 0.5, digits=1, formatter=_format_ratio),
        _Param('makeup', _('Makeup'), 0, 24, 0.5, digits=1),
    )),
    ('deesser', _('De-esser'), DeesserModel, _(
        'Softens sharp "s" and "sh" sounds: it turns down only the highs, and only while they are '
        'louder than the threshold.'
    ), (
        _Param('freq', _('Frequency'), 2000, 12000, 100, formatter=_format_freq,
               tooltip=_('Where the highs start that it listens to')),
        _Param('threshold', _('Threshold'), -60, 0, 1),
        _Param('max_cut', _('Most cut'), 0, 24, 1),
    )),
    ('limiter', _('Limiter'), LimiterModel, _(
        'Keeps the peaks below the ceiling, so nothing clips, also with an EQ boost or a fader '
        'above 0 dB. Best as the last safety after everything else.'
    ), (
        _Param('ceiling', _('Ceiling'), -24, 0, 0.5, digits=1),
    )),
)
PRESET_PLACEHOLDER = _('Presets')
# Each effect's line in the spectrum window
EFFECT_COLORS = {
    'gate': (0.55, 0.62, 0.75),
    'normalize': (0.20, 0.83, 0.60),
    'compressor': (0.98, 0.57, 0.24),
    'deesser': (0.96, 0.45, 0.71),
    'limiter': (0.94, 0.27, 0.27),
}


class EffectsWindow(Gtk.Window):
    '''
    The EQ and the effects of one device, a page each. It stays open next to the mixer, and
    every change applies right away through the "changed" signal.
    '''

    __gsignals__ = {
        'changed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
    }

    def __init__(self, device_model, loudness_available=True, **kwargs):
        super().__init__(title=_('Effects: %s') % device_model.nick, default_width=1000, default_height=600, **kwargs)
        self.add_css_class('neomixer-eq')
        self.device_model = device_model
        self.effects = device_model.effects
        self.loudness_available = loudness_available
        self._syncing = False
        self.switches = {}
        self.scales = {}
        self.values = {}

        self.eq_panel = EqPanel(device_model)
        self.eq_panel.connect('changed', lambda *_: self._edited())

        self.stack = Gtk.Stack(hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.sidebar.add_css_class('fx-sidebar')
        self.rows = {}
        for key, title, _model, description, params in EFFECTS:
            self.rows[key] = self._build_row(key, title)
            self.sidebar.append(self.rows[key])
            page = self.eq_panel if key == 'eq' else self._build_page(key, title, description, params)
            self.stack.add_named(page, key)
        self.sidebar.connect('row-selected', self._on_row_selected)

        body = Gtk.Box()
        body.append(self.sidebar)
        body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        body.append(self.stack)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(self._build_toolbar())
        main.append(body)
        self.set_child(main)

        self.show_page('eq')
        self.refresh()

    def set_text_factor(self, factor):
        self.eq_panel.set_text_factor(factor)

    def show_page(self, key):
        self.sidebar.select_row(self.rows[key])

    def _build_toolbar(self):
        toolbar = Gtk.Box(spacing=10)
        toolbar.add_css_class('eq-toolbar')

        label = Gtk.Label(label=_('All effects'))
        label.add_css_class('strip-name')
        toolbar.append(label)
        self.master_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.master_switch.set_tooltip_text(
            _('Switching off passes the audio through unchanged and keeps which effects are on, for comparing. '
              'Right clicking a strip\'s FX button does the same.')
        )
        self.master_switch.connect('notify::active', self._on_master)
        Gtk.Accessible.update_property(self.master_switch, [Gtk.AccessibleProperty.LABEL], [_('All effects')])
        toolbar.append(self.master_switch)

        self.preset_names = [PRESET_PLACEHOLDER, *PRESETS]
        self.preset_dropdown = Gtk.DropDown.new_from_strings([_(name) for name in self.preset_names])
        self.preset_dropdown.set_tooltip_text(_('Switches on the effects for one use, the EQ stays as it is'))
        self.preset_dropdown.connect('notify::selected', self._on_preset)
        toolbar.append(self.preset_dropdown)
        return toolbar

    def _build_row(self, key, title):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=12)
        box.add_css_class('fx-row')
        label = Gtk.Label(label=title, xalign=0, hexpand=True)
        box.append(label)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        switch.connect('notify::active', self._on_switch, key)
        Gtk.Accessible.update_property(switch, [Gtk.AccessibleProperty.LABEL], [title])
        box.append(switch)
        self.switches[key] = switch
        if key == 'normalize' and not self.loudness_available:
            switch.set_sensitive(False)
        row.set_child(box)
        return row

    def _build_page(self, key, title, description, params):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add_css_class('fx-page')

        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class('fx-title')
        page.append(heading)
        text = Gtk.Label(label=description, xalign=0, wrap=True, max_width_chars=70)
        text.add_css_class('fx-description')
        page.append(text)

        if key == 'normalize' and not self.loudness_available:
            missing = Gtk.Label(
                label=_('Needs PipeWire 1.4 or newer with its EBU R 128 plugin, which this system does not have.'),
                xalign=0, wrap=True
            )
            missing.add_css_class('fx-missing')
            page.append(missing)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6, margin_top=6)
        for index, param in enumerate(params):
            label = Gtk.Label(label=param.label, xalign=0)
            label.add_css_class('eq-label')
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, param.low, param.high, param.step)
            scale.set_digits(param.digits)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.add_css_class('fader')
            scale.connect('value-changed', self._on_scale, key, param)
            Gtk.Accessible.update_property(scale, [Gtk.AccessibleProperty.LABEL], [f'{title} {param.label}'])
            if param.tooltip:
                label.set_tooltip_text(param.tooltip)
                scale.set_tooltip_text(param.tooltip)
            value = Gtk.Label(xalign=1)
            value.add_css_class('eq-value')
            grid.attach(label, 0, index, 1, 1)
            grid.attach(scale, 1, index, 1, 1)
            grid.attach(value, 2, index, 1, 1)
            self.scales[(key, param.key)] = scale
            self.values[(key, param.key)] = (value, param)
        page.append(grid)

        reset = Gtk.Button(label=_('Reset'), halign=Gtk.Align.START, margin_top=6)
        reset.connect('clicked', self._on_reset, key)
        page.append(reset)
        return page

    #
    # # Showing the model
    #
    def _model(self, key):
        return self.device_model.eq if key == 'eq' else getattr(self.effects, key)

    def refresh(self):
        self._syncing = True
        self.master_switch.set_active(not self.effects.bypass)
        for key, switch in self.switches.items():
            switch.set_active(self._model(key).enabled)
        for (key, name), scale in self.scales.items():
            value = getattr(self._model(key), name)
            scale.set_value(value)
            label, param = self.values[(key, name)]
            label.set_label(param.formatter(value))
        if self.effects.bypass:
            self.add_css_class('fx-bypassed')
        else:
            self.remove_css_class('fx-bypassed')
        self._syncing = False
        self.eq_panel.refresh()

    def _edited(self):
        self.refresh()
        self.emit('changed')

    #
    # # User changes
    #
    def _on_row_selected(self, _listbox, row):
        if row is None:
            return
        key = next(key for key, candidate in self.rows.items() if candidate is row)
        self.stack.set_visible_child_name(key)

    def _on_switch(self, switch, _pspec, key):
        if self._syncing:
            return
        self._model(key).enabled = switch.get_active()
        # Switching one on while all are off would do nothing that can be heard
        if switch.get_active():
            self.effects.bypass = False
        self.show_page(key)
        self._edited()

    def _on_master(self, switch, _pspec):
        if self._syncing:
            return
        self.effects.bypass = not switch.get_active()
        self._edited()

    def _on_preset(self, dropdown, _pspec):
        index = dropdown.get_selected()
        if index == 0:
            return
        self.effects.apply_preset(self.preset_names[index])
        self.effects.bypass = False
        dropdown.set_selected(0)
        self._edited()

    def _on_scale(self, scale, key, param):
        if self._syncing:
            return
        value = round(scale.get_value() / param.step) * param.step
        setattr(self._model(key), param.key, round(value, 2))
        self._edited()

    def _on_reset(self, _button, key):
        model_class = next(model for candidate, _title, model, _text, _params in EFFECTS if candidate == key)
        enabled = self._model(key).enabled
        setattr(self.effects, key, model_class(enabled=enabled))
        self._edited()
