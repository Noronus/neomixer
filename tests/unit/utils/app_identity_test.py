import pytest

from neomixer.utils.app_identity import app_key, app_label, better_label, is_app_stream, is_system_client


@pytest.mark.parametrize('props,client_props,key', [
    ({'application.name': 'Firefox', 'application.process.binary': 'firefox'}, None, 'firefox'),
    # the stream only carries a versioned name, its client knows the process
    ({'application.name': 'Factorio 2.1.24', 'application.id': 'com.factorio.Factorio'},
     {'application.process.binary': 'factorio'}, 'factorio'),
    # no process name at all: the version must not become part of the identity
    ({'application.name': 'Factorio 2.1.24'}, None, 'factorio'),
    # flatpak ids win over the binary, which is the same for every flatpak'd electron app
    ({'application.name': 'legcord', 'application.process.binary': 'legcord',
      'pipewire.access.portal.app_id': 'app.legcord.Legcord'}, None, 'app.legcord.legcord'),
    # a host process says nothing, the program's own name does
    ({'application.name': 'Cyberpunk2077.exe', 'application.process.binary': 'wine64-preloader'},
     None, 'cyberpunk2077.exe'),
    ({'application.name': 'Minecraft', 'application.process.binary': 'java'}, None, 'minecraft'),
    # chromium names: one program, several stream names
    ({'application.name': 'WEBRTC VoiceEngine', 'application.process.binary': 'Discord'}, None, 'discord'),
    ({'application.name': 'Chromium', 'application.process.binary': 'Discord'}, None, 'discord'),
    # only a library name and a host process: fall back on the id
    ({'application.name': 'Chromium', 'application.process.binary': 'electron',
      'application.id': 'com.example.App'}, None, 'com.example.app'),
    ({'node.name': 'something'}, None, 'something'),
])
def test_app_key(props, client_props, key):
    assert app_key(props, client_props) == key


def test_different_streams_of_a_program_share_a_key():
    names = ('WEBRTC VoiceEngine', 'Chromium', 'discord_capture', 'ALSA plug-in [Discord]')
    keys = {app_key({'application.name': name, 'application.process.binary': 'Discord'}) for name in names}
    assert keys == {'discord'}


def test_label_avoids_library_names():
    assert app_label({'application.name': 'Firefox'}) == 'Firefox'
    assert app_label({'application.name': 'WEBRTC VoiceEngine', 'application.process.binary': 'signal-desktop'}) == 'Signal Desktop'
    assert app_label({'application.name': 'ringrtc', 'application.process.binary': 'discord'}) == 'Discord'


def test_better_label_prefers_what_resembles_the_key():
    assert better_label('ringrtc', 'Signal', 'signal-desktop') == 'Signal'
    assert better_label('Signal', 'ringrtc', 'signal-desktop') == 'Signal'
    assert better_label('Factorio 2.1.24', 'factorio', 'factorio') == 'Factorio 2.1.24'
    assert better_label('', 'New', 'key') == 'New'
    assert better_label('Steam Voice Settings', 'Steam', 'steam') == 'Steam'
    assert better_label('Steam', 'Steam Voice Settings', 'steam') == 'Steam'


@pytest.mark.parametrize('props', [
    {'application.name': 'in1.monitor_sink_peak', 'application.process.binary': 'python3.14', 'stream.monitor': 'true'},
    {'application.name': 'x', 'media.name': 'peak detect'},
    {'application.name': 'x', 'application.id': 'org.PulseAudio.pavucontrol'},
    {'application.name': 'x', 'application.id': 'org.kde.plasma-pa'},
    {'application.name': 'x', 'node.name': 'output.nm_route_a_to_b'},
    {'application.name': 'x', 'node.name': 'pm_route_a_to_b'},
    {'application.name': 'x', 'node.name': 'input.nm_eq_a'},
    {'application.name': 'plasmashell', 'media.role': 'event'},
    {'application.name': 'x', 'media.role': 'Notification'},
    {'media.name': 'nameless'},
])
def test_streams_that_are_not_programs_are_filtered(props):
    assert not is_app_stream(props)


def test_regular_stream_is_listed():
    assert is_app_stream({'application.name': 'Factorio 2.1.24', 'media.role': 'game'})
    assert is_app_stream({'application.process.binary': 'mpv'})


@pytest.mark.parametrize('props', [
    {'application.name': 'WirePlumber', 'application.process.binary': 'wireplumber'},
    {'application.name': 'libcanberra', 'application.process.binary': 'plasmashell', 'application.id': 'org.freedesktop.libcanberra'},
    {'application.name': '', 'application.process.binary': 'kded6'},
    {'application.name': 'xdg-desktop-portal-kde', 'application.process.binary': 'xdg-desktop-portal-kde'},
    {'application.name': 'pactl', 'application.process.binary': 'pactl'},
    {'application.name': 'in1.monitor_sink_peak', 'application.process.binary': 'python3.14'},
    {'application.name': 'pmctl', 'application.process.binary': 'python3.14'},
    {'application.name': 'pulsemeeter-listener', 'application.process.binary': 'python3.14'},
    {'application.name': 'neomixer-listener', 'application.process.binary': 'python3.14'},
    {},
])
def test_system_clients_are_not_programs(props):
    assert is_system_client(props)


@pytest.mark.parametrize('props', [
    {'application.name': 'Firefox', 'application.process.binary': 'firefox'},
    {'application.name': 'Steam Voice Settings', 'application.process.binary': 'steam'},
    {'application.name': 'legcord', 'application.process.binary': 'legcord'},
    {'application.name': 'Signal', 'application.process.binary': 'signal-desktop'},
])
def test_programs_are_not_system_clients(props):
    assert not is_system_client(props)
