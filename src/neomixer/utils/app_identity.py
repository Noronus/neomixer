'''
Works out which program a pulse stream or client belongs to.

The key has to survive restarts and updates, while stream names rarely do: a game's stream
is "Factorio 2.1.24", Chromium's is "WEBRTC VoiceEngine" and a stream may not even carry
the process name, only its client does.
'''
import re

# Processes that host other programs, their name says nothing about which one is playing
GENERIC_BINARIES = re.compile(
    r'^(python[\d.]*|java|node|electron\d*|mono|bash|sh|zsh|fish|dash|qemu.*|gjs|perl|ruby|lua.*|'
    r'wine\d*|wine\d*-preloader|wineserver|proton|pressure-vessel.*|bwrap|flatpak|snap|xdg-dbus-proxy)$',
    re.IGNORECASE
)

# Names libraries give their streams, they say nothing about the program using them
GENERIC_NAMES = re.compile(
    r'^(|webrtc voiceengine|ringrtc|chromium( input)?|libcanberra|sdl application|sdl runtime|'
    r'qtpulseaudio.*|gstreamer|openal soft|cubeb|portaudio|miniaudio|alsa plug-in.*|pipewire alsa.*|'
    r'playback|audio stream|.*_capture)$',
    re.IGNORECASE
)

TRAILING_VERSION = re.compile(r'[\s_-]*v?\d+(\.\d+)+[\w.-]*$')

# Audio plumbing and desktop services, these connect to pulse without being programs one would route
SYSTEM_BINARIES = re.compile(
    r'^(wireplumber|pipewire(-pulse)?|pw-.*|pactl|pacmd|pacat|parec|parecord|pavucontrol.*|qpwgraph|helvum|'
    r'kwin_wayland|kwin_x11|kded\d*|plasmashell|ksmserver|kscreen.*|org_kde_powerdevil|powerdevil|kmix|'
    r'xdg-desktop-portal.*|gnome-shell|gnome-settings-daemon|gsd-.*|mutter|systemd.*|neomixer|pulsemeeter|'
    r'pmctl|speech-dispatcher.*|sd_.*)$',
    re.IGNORECASE
)
SYSTEM_APP_IDS = {
    'org.freedesktop.libcanberra', 'org.kde.plasma-pa', 'org.kde.kded6', 'org.PulseAudio.pavucontrol',
    'org.kde.kmix',
}

# Stream roles of event sounds: a stream for each notification, nothing one would mix
EVENT_ROLES = {'event', 'notification', 'alert', 'a11y', 'accessibility'}


def is_system_client(props: dict) -> bool:
    '''
    True for a pulse client that is not a program of the user's: audio plumbing, desktop
    services, or a meter or loopback of NeoMixer or a similar tool
    '''
    binary = props.get('application.process.binary', '')
    name = props.get('application.name', '')
    if not binary and not name:
        return True
    if props.get('application.id') in SYSTEM_APP_IDS:
        return True
    if '_peak' in name or name in ('pmctl', 'neomixer', 'neomixer-listener'):
        return True
    return bool(SYSTEM_BINARIES.match(binary))


def is_app_stream(props: dict, media_name: str = '') -> bool:
    '''
    True for a stream that belongs in the app list: not a meter, a route of ours, an EQ, or a
    one-off event sound
    '''
    if 'application.name' not in props and 'application.process.binary' not in props:
        return False
    if '_peak' in props.get('application.name', ''):
        return False
    if str(props.get('stream.monitor')).lower() == 'true' or props.get('media.name') == 'peak detect':
        return False
    if props.get('application.id') in ('org.PulseAudio.pavucontrol', 'org.kde.plasma-pa'):
        return False
    node_name = props.get('node.name', '')
    if 'nm_route_' in node_name or 'pm_route_' in node_name or 'nm_eq_' in node_name:
        return False
    if media_name == 'audio-volume-change' or props.get('media.name') == 'audio-volume-change':
        return False
    return props.get('media.role', '').lower() not in EVENT_ROLES


def app_key(props: dict, client_props: dict | None = None) -> str:
    '''
    Stable identity of the program behind a stream or client
        "props" is the proplist of the stream or client
        "client_props" is the proplist of a stream's client, which can carry what the stream lacks
    '''
    merged = {**(client_props or {}), **props}
    portal_id = merged.get('pipewire.access.portal.app_id')
    if portal_id:
        return portal_id.lower()

    binary = merged.get('application.process.binary', '')
    if binary and not GENERIC_BINARIES.match(binary):
        return binary.lower()

    name = TRAILING_VERSION.sub('', merged.get('application.name', '')).strip()
    if name and not GENERIC_NAMES.match(name):
        return name.lower()

    app_id = merged.get('application.id')
    if app_id:
        return app_id.lower()

    return (binary or name or merged.get('node.name', 'unknown')).lower()


def app_label(props: dict, client_props: dict | None = None) -> str:
    '''
    Name to show for a stream or client, falling back on the process name for library names
    '''
    merged = {**(client_props or {}), **props}
    name = merged.get('application.name', '').strip()
    if name and not GENERIC_NAMES.match(name):
        return name

    binary = merged.get('application.process.binary', '')
    if binary:
        return binary.removesuffix('.exe').replace('-', ' ').replace('_', ' ').title()

    return name or merged.get('node.name', '') or merged.get('application.id', '') or '?'


def better_label(current: str, new: str, key: str) -> str:
    '''
    A program's clients and streams can go by different names (Signal has "ringrtc" and
    "Signal"). Prefer the one that resembles the program's key, then the first one seen.
    '''
    if not current:
        return new

    def related(label):
        lower = TRAILING_VERSION.sub('', label).lower()
        return lower and (lower in key or key in lower)

    return new if related(new) and not related(current) else current
