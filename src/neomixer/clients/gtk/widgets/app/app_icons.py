import os
import logging

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, Gtk  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger("generic")

FALLBACK_ICON = 'application-x-executable'

_desktop_apps = None


def installed_apps():
    '''
    The installed programs that show up in menus, as a list of (key, label, Gio.Icon, binary).
    The key is the one its streams get in the common case, see utils.app_identity.app_key.
    '''
    apps = []
    seen = set()
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        binary = os.path.basename(info.get_executable() or '')
        if binary in ('flatpak', 'env', 'sh', 'bash', 'snap', ''):
            # the launcher says nothing about the program, its desktop file id often does
            key = (info.get_id() or '').removesuffix('.desktop').lower()
            binary = None
        else:
            key = binary.lower()
        if key and key not in seen:
            seen.add(key)
            apps.append((key, info.get_display_name(), info.get_icon(), binary))
    return sorted(apps, key=lambda app: app[1].lower())


def _app_icons():
    global _desktop_apps
    if _desktop_apps is None:
        _desktop_apps = {key: icon for key, _label, icon, _binary in installed_apps() if icon is not None}
    return _desktop_apps


def resolve_icon(icon_name, key, binary):
    '''
    Returns the best Gio.Icon for a program. Most streams name no icon, and those that do
    often name one that is not in the theme.
    '''
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    for candidate in (icon_name, binary, key):
        if candidate and theme.has_icon(candidate):
            return Gio.ThemedIcon(name=candidate)

    for candidate in (key, (binary or '').lower()):
        icon = _app_icons().get(candidate)
        if icon is not None:
            return icon

    return Gio.ThemedIcon(name=FALLBACK_ICON)
