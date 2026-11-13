import gettext

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib, GObject, Gtk, Pango  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

WINDOW_WIDTH = 520


class PhoneWindow(Gtk.Window):
    '''
    Whether the Android app may connect, where it finds the mixer, and which phones were allowed.
    '''

    __gsignals__ = {
        'enabled_changed': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (bool,)),
        'forget': (GObject.SignalFlags.RUN_FIRST, GObject.TYPE_NONE, (str,)),
    }

    def __init__(self, **kwargs):
        super().__init__(title=_('Phone'), default_width=WINDOW_WIDTH, **kwargs)
        self.add_css_class('neomixer')

        intro = Gtk.Label(
            xalign=0, wrap=True,
            label=_('The NeoMixer app for Android controls this mixer from your phone. With its audio switched on, '
                    'the phone is an output to route to, and its microphone an input.')
        )
        intro.add_css_class('hotkey-hint')

        self.enabled = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._enabled_handler = self.enabled.connect('notify::active', self._on_enabled)
        enabled_row = Gtk.Box(spacing=12)
        enabled_row.append(Gtk.Label(label=_('Let phones on this network connect'), xalign=0, hexpand=True))
        enabled_row.append(self.enabled)

        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False)
        self.firewall = Gtk.Label(xalign=0, wrap=True)
        self.firewall.add_css_class('hotkey-hint')

        title = Gtk.Label(label=_('ALLOWED PHONES'), xalign=0, margin_top=8)
        title.add_css_class('section-title')
        self.phones = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        note = Gtk.Label(
            xalign=0, wrap=True,
            label=_('A new phone asks here once before it is let in. Nothing is encrypted: use it on a network you trust.')
        )
        note.add_css_class('hotkey-hint')

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_start=16, margin_end=16, margin_top=14, margin_bottom=14)
        for widget in (intro, enabled_row, self.status, self.firewall, title, self.phones, note):
            main.append(widget)
        self.set_child(main)

    def show_state(self, *, enabled, running, error, address, port, phones, connected):
        '''
            "phones" are the allowed ones as (id, name), "connected" the ids of those here now
        '''
        self.enabled.handler_block(self._enabled_handler)
        self.enabled.set_active(enabled)
        self.enabled.handler_unblock(self._enabled_handler)

        self.status.remove_css_class('hotkey-status-problem')
        self.status.remove_css_class('hotkey-status-good')
        if error:
            self.status.set_label(_('Port %(port)s cannot be used: %(error)s') % {'port': port, 'error': error})
            self.status.add_css_class('hotkey-status-problem')
        elif running:
            where = f'{address}:{port}' if address else str(port)
            self.status.set_label(_('The app finds this mixer by itself. If it does not, enter %s in the app.') % where)
            self.status.add_css_class('hotkey-status-good')
        else:
            self.status.set_label(_('Switched off, no phone can connect.'))

        self.firewall.set_visible(running)
        self.firewall.set_label(
            _('With a firewall on this machine, allow TCP and UDP ports %(first)s and %(second)s from your network.')
            % {'first': port, 'second': port + 1}
        )

        while (child := self.phones.get_first_child()) is not None:
            self.phones.remove(child)

        if not phones:
            empty = Gtk.Label(label=_('None yet. Open the app on your phone.'), xalign=0)
            empty.add_css_class('hotkey-hint')
            self.phones.append(empty)

        for phone_id, name in phones:
            row = Gtk.Box(spacing=10)
            label = Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            label.add_css_class('hotkey-device')
            row.append(label)
            if phone_id in connected:
                here = Gtk.Label(label=_('connected'))
                here.add_css_class('hotkey-status-good')
                row.append(here)
            forget = Gtk.Button(label=_('Forget'))
            forget.set_tooltip_text(_('This phone has to ask again before it can connect'))
            forget.connect('clicked', self._on_forget, phone_id)
            row.append(forget)
            self.phones.append(row)

    def _on_enabled(self, switch, _param):
        self.emit('enabled_changed', switch.get_active())

    def _on_forget(self, _button, phone_id):
        self.emit('forget', phone_id)


class PairWindow(Gtk.Window):
    '''
    Asks if a phone that was never seen may control the mixer. Closing it says no.
    '''

    def __init__(self, name, address, answer, **kwargs):
        super().__init__(title=_('A phone wants to connect'), resizable=False, **kwargs)
        self.add_css_class('neomixer')
        self._answer = answer

        question = Gtk.Label(xalign=0, wrap=True, max_width_chars=44)
        question.set_markup(
            _('<b>%(name)s</b> (%(address)s) wants to control NeoMixer, play through it and send its microphone.')
            % {'name': GLib.markup_escape_text(name), 'address': GLib.markup_escape_text(address)}
        )
        hint = Gtk.Label(label=_('Only allow it if that is your phone asking right now.'), xalign=0, wrap=True, max_width_chars=44)
        hint.add_css_class('hotkey-hint')

        deny = Gtk.Button(label=_('Deny'))
        deny.connect('clicked', lambda _: self._decide(False))
        allow = Gtk.Button(label=_('Allow'))
        allow.add_css_class('suggested-action')
        allow.connect('clicked', lambda _: self._decide(True))
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=6)
        buttons.append(deny)
        buttons.append(allow)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_start=18, margin_end=18, margin_top=16, margin_bottom=16)
        main.append(question)
        main.append(hint)
        main.append(buttons)
        self.set_child(main)
        self.connect('close-request', self._on_close)

    def _decide(self, allowed):
        answer, self._answer = self._answer, None
        if answer is not None:
            answer(allowed)
        self.destroy()

    def _on_close(self, _window):
        self._decide(False)
        return True
