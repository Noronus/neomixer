import gettext

from neomixer.controller.hotkey_controller import (
    ACTION_LABELS, NOTICE_APPLIED, NOTICE_CANCELLED, NOTICE_FAILED
)
from neomixer.model.types import DEVICE_TYPE_SHORT
from neomixer.utils.hotkeys import ACTIONS, make_trigger, shortcut_id, trigger_label, works_alone

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GObject, Gtk, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

NO_KEY = '—'
MIN_STEP = 1
MAX_STEP = 25
MAX_LIST_HEIGHT = 640

MODIFIER_KEYS = {
    'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Meta_L', 'Meta_R', 'Super_L', 'Super_R',
    'Hyper_L', 'Hyper_R', 'ISO_Level3_Shift', 'ISO_Level5_Shift', 'Caps_Lock', 'Num_Lock',
}
LOGO_MASK = Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.HYPER_MASK


def modifier_names(state):
    '''The names of the modifiers in a key event's state.'''
    names = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        names.append('CTRL')
    if state & Gdk.ModifierType.ALT_MASK:
        names.append('ALT')
    if state & Gdk.ModifierType.SHIFT_MASK:
        names.append('SHIFT')
    if state & LOGO_MASK:
        names.append('LOGO')
    return names


def is_symbol(keyval):
    '''A key that types a character with no upper and lower case: ! or 1, and not m, F5 or the space bar.'''
    character = chr(Gdk.keyval_to_unicode(keyval))
    return (character.isprintable() and not character.isspace() and character != '\0'
            and Gdk.keyval_to_lower(keyval) == Gdk.keyval_to_upper(keyval)
            and not Gdk.keyval_name(keyval).startswith('KP_'))


class HotkeyWindow(Gtk.Window):
    '''
    The keys of every device. Changes are gathered and go to the desktop together, it asks about
    new keys and one question is better than one for each. Closing the window applies them too.
    '''

    __gsignals__ = {
        'apply': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (GObject.TYPE_PYOBJECT,)),
        'configure': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, ()),
        'step_changed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (int,)),
    }

    def __init__(self, step, **kwargs):
        super().__init__(title=_('Hotkeys'), default_width=760, **kwargs)
        self.add_css_class('neomixer')
        self.devices = []
        self.saved = {}  # (device_type, device_id, action): keys
        self.edits = {}  # the same, not applied yet. None removes it.
        self.buttons = {}
        self.capturing = None
        self.state = None
        self.problem = None

        self.intro = Gtk.Label(xalign=0, wrap=True)
        self.intro.add_css_class('hotkey-hint')
        self.command = Gtk.Label(xalign=0, selectable=True, focusable=False, visible=False)
        self.grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        # As tall as it takes, until it would not fit on a screen
        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self.grid, propagate_natural_height=True,
                                      max_content_height=MAX_LIST_HEIGHT)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.step = Gtk.SpinButton.new_with_range(MIN_STEP, MAX_STEP, 1)
        self.step.set_value(step)
        self.step.set_numeric(True)
        self.step.connect('value-changed', lambda spin: self.emit('step_changed', spin.get_value_as_int()))
        step_label = Gtk.Label(label=_('Volume step per press (%)'), xalign=0)
        step_row = Gtk.Box(spacing=8)
        step_row.set_tooltip_text(_('Holding a key keeps going, up to 0 dB. Louder than that takes a press each.'))
        step_row.append(step_label)
        step_row.append(self.step)

        self.status = Gtk.Label(xalign=0, wrap=True, hexpand=True, natural_wrap_mode=Gtk.NaturalWrapMode.WORD)
        self.configure_button = Gtk.Button(label=_('Desktop settings'), visible=False)
        self.configure_button.set_tooltip_text(_("Open your desktop's own settings for these keys"))
        self.configure_button.connect('clicked', lambda _: self.emit('configure'))
        self.apply_button = Gtk.Button(label=_('Apply'), sensitive=False)
        self.apply_button.connect('clicked', lambda _: self.apply())

        buttons = Gtk.Box(spacing=8)
        buttons.append(self.status)
        buttons.append(self.configure_button)
        buttons.append(self.apply_button)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_start=16, margin_end=16, margin_top=14, margin_bottom=14)
        main.append(self.intro)
        main.append(self.command)
        main.append(scrolled)
        main.append(step_row)
        main.append(buttons)
        self.set_child(main)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self.on_key_pressed)
        self.add_controller(keys)
        self.connect('close-request', self._on_close_request)
        self._show_status()

    def set_devices(self, devices):
        '''
            "devices" are (device_type, device_id, nick, {action: keys})
        '''
        rows_changed = [device[:3] for device in devices] != [device[:3] for device in self.devices]
        self.devices = devices
        self.saved = {
            (device_type, device_id, action): trigger
            for device_type, device_id, _nick, hotkeys in devices
            for action, trigger in hotkeys.items()
        }
        known = {(device_type, device_id) for device_type, device_id, *_ in devices}
        self.edits = {key: trigger for key, trigger in self.edits.items() if key[:2] in known}
        if self.capturing is not None and self.capturing[:2] not in known:
            self.capturing = None
        if rows_changed:
            self._build_grid()
        self._refresh_buttons()
        self._show_status()

    def set_state(self, state):
        '''
            "state" has available, can_configure, bound, busy and notice, see HotkeyController
        '''
        self.state = state
        for button in self.buttons.values():
            button.set_sensitive(state.available is not False)
        self.configure_button.set_visible(state.can_configure)
        self._refresh_buttons()
        self._show_status()

    def _build_grid(self):
        while (child := self.grid.get_first_child()) is not None:
            self.grid.remove(child)
        self.buttons = {}

        for column, action in enumerate(ACTIONS):
            title = Gtk.Label(label=ACTION_LABELS[action])
            title.add_css_class('hotkey-column')
            self.grid.attach(title, column + 1, 0, 1, 1)

        row = 1
        current_type = None
        for device_type, device_id, nick, _hotkeys in self.devices:
            if device_type != current_type:
                current_type = device_type
                section = Gtk.Box(margin_top=8)
                section.add_css_class(f'section-{device_type}')
                title = Gtk.Label(label=DEVICE_TYPE_SHORT[device_type].upper(), xalign=0)
                title.add_css_class('section-title')
                section.append(title)
                self.grid.attach(section, 0, row, 4, 1)
                row += 1

            label = Gtk.Label(label=nick, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=18)
            label.add_css_class('hotkey-device')
            label.set_tooltip_text(nick)
            self.grid.attach(label, 0, row, 1, 1)
            for column, action in enumerate(ACTIONS):
                key = (device_type, device_id, action)
                button = Gtk.Button()
                button.add_css_class('hotkey-key')
                button.set_tooltip_text(_('Click, then press the keys. Esc cancels, Backspace removes the key.'))
                button.connect('clicked', self.on_key_button_clicked, key)
                button.set_sensitive(self.state is None or self.state.available is not False)
                Gtk.Accessible.update_property(
                    button, [Gtk.AccessibleProperty.LABEL], [f'{nick}: {ACTION_LABELS[action]}']
                )
                self.grid.attach(button, column + 1, row, 1, 1)
                self.buttons[key] = button
            row += 1
        self._refresh_buttons()

    def trigger_of(self, key):
        '''The keys of an action as they will be: an edit if there is one, otherwise what is saved.'''
        return self.edits[key] if key in self.edits else self.saved.get(key)

    def label_of(self, key):
        trigger = self.trigger_of(key)
        if not trigger:
            return NO_KEY
        # The desktop has the last word, the keys may be different in its settings
        if key not in self.edits and self.state is not None:
            shown = self.state.bound.get(shortcut_id(*key, trigger))
            if shown:
                return shown
        return trigger_label(trigger)

    def _refresh_buttons(self):
        for key, button in self.buttons.items():
            if key == self.capturing:
                button.set_label(_('Press keys…'))
                button.add_css_class('hotkey-capturing')
            else:
                button.set_label(self.label_of(key))
                button.remove_css_class('hotkey-capturing')
        self.apply_button.set_sensitive(bool(self.edits) and not (self.state is not None and self.state.busy))

    def _show_status(self):
        available = self.state.available if self.state is not None else None
        self.apply_button.set_visible(available is not False)
        self.command.set_visible(available is False)
        if available is False:
            example = self.devices[0][:2] if self.devices else ('vi', '1')
            self.intro.set_label(
                _('This desktop gives programs no global keys. Set them up in its own settings, with commands '
                  'like this one. The last word is mute, up or down.')
            )
            self.command.set_label('neomixer hotkey %s %s mute' % example)
        else:
            self.intro.set_label(_('Click a field, then press the keys. Esc cancels, Backspace removes the key.'))

        text, css_class = self._status_text()
        self.status.set_label(text)
        self.status.remove_css_class('hotkey-status-problem')
        self.status.remove_css_class('hotkey-status-good')
        if css_class:
            self.status.add_css_class(css_class)

    def _status_text(self):
        if self.problem:
            return self.problem, 'hotkey-status-problem'
        if self.state is None or self.state.available is not True:
            return '', None
        if self.state.busy:
            return _('Your desktop has to confirm new keys, look for its window.'), None
        if self.edits:
            return _('Not applied yet.'), None
        notices = {
            NOTICE_APPLIED: (_('The keys work now, also with the window closed.'), 'hotkey-status-good'),
            NOTICE_CANCELLED: (_('Your desktop did not take those keys, so they are not set.'), 'hotkey-status-problem'),
            NOTICE_FAILED: (_('Your desktop could not be asked. The keys stay saved for the next start.'), 'hotkey-status-problem'),
        }
        return notices.get(self.state.notice, ('', None))

    def on_key_button_clicked(self, _, key):
        self.problem = None
        self.capturing = None if self.capturing == key else key
        self._refresh_buttons()
        self._show_status()

    def on_key_pressed(self, _controller, keyval, _keycode, state):
        if self.capturing is None:
            return False

        name = Gdk.keyval_name(keyval)
        if name in MODIFIER_KEYS:
            return True

        modifiers = modifier_names(state)
        if not modifiers and keyval == Gdk.KEY_Escape:
            self._finish_capture()
        elif not modifiers and keyval == Gdk.KEY_BackSpace:
            self._set_trigger(None)
        else:
            # A symbol stands for shift already: KDE only knows Ctrl+! and never fires on Ctrl+Shift+1
            if is_symbol(keyval) and 'SHIFT' in modifiers:
                modifiers.remove('SHIFT')
            self._take_key(modifiers, Gdk.keyval_name(Gdk.keyval_to_lower(keyval)))
        return True

    def _take_key(self, modifiers, key):
        if not modifiers and not works_alone(key):
            self.problem = _('%s alone would stop working for typing. Add Ctrl, Alt, Shift or Meta.') % trigger_label(key)
            self._show_status()
            return

        trigger = make_trigger(modifiers, key)
        for device_type, device_id, nick, _hotkeys in self.devices:
            for action in ACTIONS:
                other = (device_type, device_id, action)
                if other != self.capturing and self.trigger_of(other) == trigger:
                    self.problem = _('%s is already used for %s: %s') % (trigger_label(trigger), nick, ACTION_LABELS[action])
                    self._show_status()
                    return

        self._set_trigger(trigger)

    def _set_trigger(self, trigger):
        key = self.capturing
        if trigger == self.saved.get(key):
            self.edits.pop(key, None)
        else:
            self.edits[key] = trigger
        self._finish_capture()

    def _finish_capture(self):
        self.capturing = None
        self.problem = None
        self._refresh_buttons()
        self._show_status()

    def apply(self):
        edits, self.edits = self.edits, {}
        self.problem = None
        self.capturing = None
        if edits:
            self.emit('apply', edits)
        self._refresh_buttons()
        self._show_status()

    def _on_close_request(self, _):
        self.apply()
        return False
