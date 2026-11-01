import os

from neomixer.settings import DATA_DIR

# pylint: disable=wrong-import-order,wrong-import-position
from gi import require_version as gi_require_version
gi_require_version('Gtk', '4.0')
from gi.repository import Gdk, Gtk  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

# Above Gtk.STYLE_PROVIDER_PRIORITY_USER: the console look is the app's own, a desktop's gtk.css
# (KDE writes one) should only reach what the app does not style itself
STYLE_PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_USER + 100


def load():
    '''Load the app's style sheet and icons, needs the display to be open.'''
    display = Gdk.Display.get_default()

    provider = Gtk.CssProvider()
    provider.load_from_path(os.path.join(DATA_DIR, 'neomixer.css'))
    Gtk.StyleContext.add_provider_for_display(display, provider, STYLE_PRIORITY)

    # The look is the same on every desktop. The style sheet builds on GTK's own theme: another one
    # brings its colors along for what is not styled here, like dark text from a light KDE theme,
    # where asking for dark does not help unless the theme has a variant for it.
    settings = Gtk.Settings.get_for_display(display)
    settings.set_property('gtk-theme-name', 'Default')

    # The newer property replaces the older one, which complains since GTK 4.20
    if settings.find_property('gtk-interface-color-scheme') is not None:
        settings.set_property('gtk-interface-color-scheme', Gtk.InterfaceColorScheme.DARK)
    else:
        settings.set_property('gtk-application-prefer-dark-theme', True)
    Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(DATA_DIR, 'icons'))
