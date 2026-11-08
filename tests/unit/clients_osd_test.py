import pytest

from neomixer.clients.gtk.osd import VolumeOsd, describe, volume_icon

from gi.repository import GLib


class FakeBus:
    '''Takes the calls, and answers them when told to.'''

    def __init__(self, fails=()):
        self.calls = []  # (name, method, parameters)
        self.fails = fails
        self.next_id = 7

    def call(self, name, _path, _interface, method, parameters, _reply, _flags, _timeout, _cancel, callback, data):
        self.calls.append((name, method, parameters.unpack()))
        callback(self, (name, method), data)

    def call_finish(self, result):
        name, _method = result
        if name in self.fails:
            raise GLib.Error('no such name')
        return GLib.Variant('(u)', (self.next_id,))


@pytest.mark.parametrize('volume, text, bar', [
    (100, '0.0 dB', 100),
    (50, '-18.1 dB', 50),
    (115, '+3.6 dB · 115%', None),  # KDE's bar ends at 100 %, it would say 100 % there
    (0, '-∞ dB', None),
    (0.4, '-143.9 dB', 1),  # 0 would read as muted
])
def test_what_it_says(volume, text, bar):
    _icon, said, shown_bar = describe(volume, False)
    assert (said, shown_bar) == (text, bar)


def test_muted_says_so_and_what_it_will_be():
    icon, text, bar = describe(80, True)
    assert icon == 'audio-volume-muted'
    assert text == 'muted, -5.8 dB'
    assert bar is None


@pytest.mark.parametrize('volume, mute, icon', [
    (0, False, 'audio-volume-muted'),
    (80, True, 'audio-volume-muted'),
    (10, False, 'audio-volume-low'),
    (50, False, 'audio-volume-medium'),
    (100, False, 'audio-volume-high'),
    (101, False, 'audio-volume-high-warning'),
])
def test_icons_follow_the_level(volume, mute, icon):
    assert volume_icon(volume, mute, False) == icon


def test_inputs_have_microphones():
    assert volume_icon(80, False, True) == 'microphone-sensitivity-high'
    assert volume_icon(80, True, True) == 'microphone-sensitivity-muted'


def test_kde_shows_its_own_volume_osd_with_the_name():
    bus = FakeBus()
    VolumeOsd('KDE', bus).show('Music', 90, False)
    assert bus.calls == [('org.kde.plasmashell', 'mediaPlayerVolumeChanged', (90, 'Music: -2.7 dB', 'audio-volume-high'))]


def test_kde_says_it_without_a_bar_where_the_bar_ends():
    bus = FakeBus()
    osd = VolumeOsd('KDE', bus)
    osd.show('Music', 115, False)
    osd.show('Music', 90, True)
    assert [call[1:] for call in bus.calls] == [
        ('showText', ('audio-volume-high-warning', 'Music: +3.6 dB · 115%')),
        ('showText', ('audio-volume-muted', 'Music: muted, -2.7 dB')),
    ]


def test_elsewhere_a_notification_that_replaces_the_last():
    bus = FakeBus()
    osd = VolumeOsd('GNOME', bus)
    osd.show('Music', 90, False)
    osd.show('Music', 95, False)
    (name, method, first), (_, _, second) = bus.calls
    assert (name, method) == ('org.freedesktop.Notifications', 'Notify')
    app, replaces, icon, summary, body, _actions, hints, _timeout = first
    assert (app, replaces, icon, summary, body) == ('NeoMixer', 0, 'audio-volume-high', 'Music', '-2.7 dB')
    assert hints['value'] == 90 and hints['transient'] is True
    assert second[1] == 7, 'the second replaces the first'


def test_kde_without_its_shell_falls_back_to_a_notification():
    bus = FakeBus(fails=('org.kde.plasmashell',))
    VolumeOsd('KDE', bus).show('Music', 90, False)
    assert [call[:2] for call in bus.calls] == [
        ('org.kde.plasmashell', 'mediaPlayerVolumeChanged'),
        ('org.freedesktop.Notifications', 'Notify'),
    ]


def test_no_notification_daemon_is_no_error():
    bus = FakeBus(fails=('org.freedesktop.Notifications',))
    VolumeOsd('', bus).show('Music', 90, False)
    assert len(bus.calls) == 1


def test_kde_is_found_in_a_list_of_desktops():
    assert VolumeOsd('KDE', FakeBus()).kde
    assert VolumeOsd('ubuntu:KDE', FakeBus()).kde
    assert not VolumeOsd('GNOME', FakeBus()).kde
