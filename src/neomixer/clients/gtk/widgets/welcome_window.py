import gettext

from neomixer.settings import VERSION

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib

_ = gettext.gettext

UPSTREAM_URL = 'https://github.com/theRealCarneiro/pulsemeeter'
APP_ICON_NAME = 'org.neomixer.NeoMixer'
FALLBACK_ICON_NAME = 'multimedia-volume-control'

GUIDE_SECTIONS = [
    (
        _('What is NeoMixer?'),
        _('NeoMixer is a virtual audio mixer and router built on top of PipeWire. '
          'It lets you create virtual devices and freely route audio between your '
          'inputs and outputs, with volume, mute and an equalizer on the way.')
    ),
    (
        _('The four device types'),
        _('<b>Hardware Inputs</b> are physical sources such as microphones and line-in.\n'
          '<b>Virtual Inputs</b> are virtual sinks that applications can play into.\n'
          '<b>Hardware Outputs</b> are physical sinks such as speakers and headphones.\n'
          '<b>Virtual Outputs</b> are virtual sources that applications can capture from.')
    ),
    (
        _('Routing'),
        _('Audio always flows from an input to an output: the A and B buttons on each input '
          'send its audio to that output. Right click one for its channel mapping and its '
          'own volume.')
    ),
    (
        _('Programs'),
        _('Every program that connects to the sound server stays in the list, whether it is '
          'playing or not. What you choose for a program is remembered: its streams go to that '
          'output whenever it starts, also after a reboot. You can set a program up while it is '
          'not running, or add one with the + button before it ever played anything.')
    ),
    (
        _('Equalizer'),
        _('The EQ button on an input opens its parametric equalizer. Drag a band on the curve '
          'to change its frequency and gain, scroll to change its width. Switching it off passes '
          'the audio through unchanged, so you can compare. Right clicking the EQ button does '
          'that without opening the window.')
    ),
    (
        _('Running in the background'),
        _('New streams only go to their remembered output while NeoMixer runs, so closing the '
          'window keeps it running with an icon in the tray. Routes and the EQ also keep working '
          'after you quit, until you log out. Turn on "Start at login" to have all of it from '
          'the start.')
    ),
    (
        _('Primary device'),
        _('Marking a device as primary (★) makes it the default that new applications attach '
          'to automatically. This sets the primary in PipeWire itself.')
    ),
]

FOOTER = _('You can reopen this guide any time from the settings menu.')


class WelcomeWindow(Gtk.Window):
    '''
    Wwindow introducing NeoMixer to new users.
    '''

    def __init__(self, **kwargs):
        super().__init__(title=_('Welcome to NeoMixer!'), **kwargs)
        self.add_css_class('neomixer')
        self.set_default_size(560, 640)

        mainbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                          margin_start=18, margin_end=18, margin_top=18, margin_bottom=18)

        mainbox.append(self._build_header())

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        mainbox.append(separator)

        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._build_guide())
        mainbox.append(scrolled)

        footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                             valign=Gtk.Align.END)

        footer = Gtk.Label(halign=Gtk.Align.START, xalign=0, hexpand=True,
                          valign=Gtk.Align.CENTER)
        footer.set_wrap(True)
        footer.set_markup('<span alpha="65%%">%s</span>'
                          % GLib.markup_escape_text(FOOTER))
        footer_box.append(footer)

        close_button = Gtk.Button(label=_('Get started'), valign=Gtk.Align.CENTER)
        close_button.connect('clicked', lambda _: self.close())
        footer_box.append(close_button)

        mainbox.append(footer_box)

        self.set_child(mainbox)

    def _build_header(self):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        icon = Gtk.Image.new_from_icon_name(self._resolve_icon_name())
        icon.set_pixel_size(96)
        icon.set_valign(Gtk.Align.CENTER)
        header.append(icon)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            valign=Gtk.Align.CENTER, hexpand=True)

        title = Gtk.Label(halign=Gtk.Align.START)
        title.set_markup('<span size="xx-large" weight="bold">%s</span>'
                         % GLib.markup_escape_text(_('Welcome to NeoMixer!')))
        title_box.append(title)

        version = Gtk.Label(halign=Gtk.Align.START)
        version.set_markup('<span alpha="65%%">%s</span>'
                           % GLib.markup_escape_text(_('Version %s') % VERSION))
        title_box.append(version)

        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                        halign=Gtk.Align.START)

        source_link = Gtk.Label()
        source_link.set_markup('<a href="%s">%s</a>'
                               % (UPSTREAM_URL, GLib.markup_escape_text(_('Based on Pulsemeeter'))))
        links.append(source_link)

        title_box.append(links)

        header.append(title_box)
        return header

    def _build_guide(self):
        guide = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=6, margin_bottom=6, margin_start=2, margin_end=2)

        for heading, body in GUIDE_SECTIONS:
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            heading_label = Gtk.Label(halign=Gtk.Align.START)
            heading_label.set_markup('<span size="large" weight="bold">%s</span>'
                                     % GLib.markup_escape_text(heading))
            section.append(heading_label)

            body_label = Gtk.Label(halign=Gtk.Align.START, xalign=0)
            body_label.set_wrap(True)
            body_label.set_markup(body)
            section.append(body_label)

            guide.append(section)

        return guide

    @staticmethod
    def _resolve_icon_name():
        '''
        Use the installed themed app icon when available, otherwise fall back to a
        stock icon. (In instances of cloned repo instead of installed)
        '''
        display = Gdk.Display.get_default()
        if display is not None:
            icon_theme = Gtk.IconTheme.get_for_display(display)
            if icon_theme.has_icon(APP_ICON_NAME):
                return APP_ICON_NAME
        return FALLBACK_ICON_NAME
