'''
A device's volume on screen, the way the desktop shows its own: for a hotkey used while the mixer
is not in front, where nothing else would say how loud it is now, or where 0 dB is.

KDE has the system volume's OSD for every program, with a name, a bar and the percentage. Elsewhere
it is a notification that replaces the one before, most notification daemons draw the bar too.
'''
import gettext
import logging
import os

from neomixer.clients.gtk.widgets.common.volume_widget import UNITY, format_db

# pylint: disable=wrong-import-order,wrong-import-position
from gi.repository import Gio, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

LOG = logging.getLogger('generic')

_ = gettext.gettext

KDE_OSD = ('org.kde.plasmashell', '/org/kde/osdService', 'org.kde.osdService')
NOTIFICATIONS = ('org.freedesktop.Notifications', '/org/freedesktop/Notifications', 'org.freedesktop.Notifications')
NOTIFICATION_MS = 2000
CALL_TIMEOUT_MS = 1000

# Breeze's own levels, from plasma-pa
LOW = 25
MEDIUM = 75
# KDE's bar ends at 100 %, louder than 0 dB it would say 100 % too
BAR_MAX = UNITY


def volume_icon(volume, mute, is_source):
    '''The icon for a level, as the desktop's own volume and microphone OSDs have them.'''
    if is_source:
        names = ('microphone-sensitivity-muted', 'microphone-sensitivity-low', 'microphone-sensitivity-medium',
                 'microphone-sensitivity-high', 'microphone-sensitivity-high')
    else:
        names = ('audio-volume-muted', 'audio-volume-low', 'audio-volume-medium', 'audio-volume-high',
                 'audio-volume-high-warning')
    if mute or volume <= 0:
        return names[0]
    if volume < LOW:
        return names[1]
    if volume < MEDIUM:
        return names[2]
    return names[3] if volume <= UNITY else names[4]


def describe(volume, mute, is_source=False):
    '''
    What the OSD shows: (icon, text, bar). The text is dB, as on the strip; the bar is the percentage,
    full at 0 dB, or None for no bar: muted, silent, or louder than the bar can show.
    '''
    icon = volume_icon(volume, mute, is_source)
    db = format_db(volume)
    if mute:
        return icon, _('muted, %s') % db, None
    if volume <= 0:
        return icon, db, None
    if volume > BAR_MAX:
        return icon, '%s · %s' % (db, _('%d%%') % round(volume)), None
    # Some desktops take 0 for muted
    return icon, db, max(1, round(volume))


class VolumeOsd:

    def __init__(self, desktops=None, bus=None):
        '''
            "desktops" is XDG_CURRENT_DESKTOP, "bus" the session bus: both for tests
        '''
        if desktops is None:
            desktops = os.environ.get('XDG_CURRENT_DESKTOP', '')
        self.kde = 'KDE' in desktops.split(':')
        self._bus = bus
        self._notification = 0  # the one to replace

    @property
    def bus(self):
        if self._bus is None:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        return self._bus

    def show(self, nick, volume, mute, is_source=False):
        icon, text, bar = describe(volume, mute, is_source)
        try:
            if self.kde:
                self._show_on_kde(nick, icon, text, bar)
            else:
                self._notify(nick, icon, text, bar)
        except GLib.Error:
            LOG.exception('Could not show the volume')

    def _show_on_kde(self, nick, icon, text, bar):
        line = f'{nick}: {text}'
        if bar is None:
            method, parameters = 'showText', GLib.Variant('(ss)', (icon, line))
        else:
            method, parameters = 'mediaPlayerVolumeChanged', GLib.Variant('(iss)', (bar, line, icon))
        self.bus.call(
            *KDE_OSD, method, parameters, None, Gio.DBusCallFlags.NO_AUTO_START, CALL_TIMEOUT_MS, None,
            self._on_kde_shown, (nick, icon, text, bar)
        )

    def _on_kde_shown(self, bus, result, shown):
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            # Plasma restarting, or KDE without its shell: the notification still says it
            LOG.info('KDE did not show the volume, as a notification instead: %s', error.message)
            self._notify(*shown)

    def _notify(self, nick, icon, text, bar):
        hints = {
            'transient': GLib.Variant('b', True),
            'urgency': GLib.Variant('y', 0),
            # One for all of them, with the daemons that know what replaces_id is for and those that do not
            'x-canonical-private-synchronous': GLib.Variant('s', 'neomixer-volume'),
            'x-dunst-stack-tag': GLib.Variant('s', 'neomixer-volume'),
        }
        if bar is not None:
            hints['value'] = GLib.Variant('i', bar)
        parameters = GLib.Variant(
            '(susssasa{sv}i)', ('NeoMixer', self._notification, icon, nick, text, [], hints, NOTIFICATION_MS)
        )
        self.bus.call(
            *NOTIFICATIONS, 'Notify', parameters, GLib.VariantType('(u)'), Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS,
            None, self._on_notified, None
        )

    def _on_notified(self, bus, result, _):
        try:
            self._notification = bus.call_finish(result).unpack()[0]
        except GLib.Error as error:
            LOG.info('Could not show the volume: %s', error.message)
