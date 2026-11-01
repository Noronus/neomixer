'''
Presses real keys and moves the real pointer in a KWin, through the way in that KDE itself has for
tools like this. It needs KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 on KWin, for a compositor in a test.

    kde_keys.py ctrl alt m            press them together, and let go
    kde_keys.py --hold 1.2 ctrl alt u  the same, holding them down that long
    kde_keys.py --click 800 500       a left click there
    kde_keys.py --screenshot out.png  the whole screen
'''
import argparse
import importlib
import os
import subprocess
import sys
import tempfile
import time

PROTOCOL = '/usr/share/plasma-wayland-protocols/fake-input.xml'
SCANNER = (sys.executable, '-m', 'pywayland.scanner')

LETTERS = dict(zip('qwertyuiop', range(16, 26))) | dict(zip('asdfghjkl', range(30, 39))) | dict(zip('zxcvbnm', range(44, 51)))
DIGITS = {str((i + 1) % 10): 2 + i for i in range(10)}
FUNCTIONS = {f'f{i + 1}': 59 + i for i in range(10)} | {'f11': 87, 'f12': 88}
FUNCTIONS |= {f'f{i + 13}': 183 + i for i in range(12)}
NAMED = {
    'esc': 1, 'backspace': 14, 'tab': 15, 'enter': 28, 'ctrl': 29, 'shift': 42, 'alt': 56, 'space': 57,
    'meta': 125, 'left': 105, 'right': 106, 'up': 103, 'down': 108,
}
KEYS = LETTERS | DIGITS | FUNCTIONS | NAMED
BUTTON_LEFT = 0x110


def load_protocol():
    target = tempfile.mkdtemp(prefix='fake-input-')
    subprocess.run([*SCANNER, '-i', PROTOCOL, '-o', os.path.join(target, 'protocols')], check=True, capture_output=True)
    sys.path.insert(0, target)
    return importlib.import_module('protocols.fake_input').OrgKdeKwinFakeInput


def screenshot(path):
    '''Through KWin's own interface, a window of another program cannot be drawn from here.'''
    import gi
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gio, GLib, GdkPixbuf

    read_end, write_end = os.pipe()
    descriptors = Gio.UnixFDList.new()
    index = descriptors.append(write_end)
    reply, _ = Gio.bus_get_sync(Gio.BusType.SESSION).call_with_unix_fd_list_sync(
        'org.kde.KWin.ScreenShot2', '/org/kde/KWin/ScreenShot2', 'org.kde.KWin.ScreenShot2', 'CaptureWorkspace',
        GLib.Variant('(a{sv}h)', ({}, index)), None, Gio.DBusCallFlags.NONE, 10000, descriptors, None
    )
    os.close(write_end)
    meta = reply.unpack()[0]
    width, height, stride = meta['width'], meta['height'], meta['stride']
    data = b''
    while len(data) < stride * height:
        data += os.read(read_end, 1 << 20)
    GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride
    ).savev(path, 'png', [], [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hold', type=float, default=0.05)
    parser.add_argument('--click', nargs=2, type=int)
    parser.add_argument('--screenshot')
    parser.add_argument('keys', nargs='*')
    args = parser.parse_args()

    if args.screenshot:
        screenshot(args.screenshot)
        return

    from pywayland.client import Display
    fake_input_class = load_protocol()
    display = Display()
    display.connect()
    found = {}
    registry = display.get_registry()

    def on_global(registry, name, interface, version):
        if interface == 'org_kde_kwin_fake_input':
            found['input'] = registry.bind(name, fake_input_class, version)

    registry.dispatcher['global'] = on_global
    display.roundtrip()
    fake = found['input']
    fake.authenticate('NeoMixer tests', 'pressing keys in a test compositor')
    display.roundtrip()

    if args.click:
        fake.pointer_motion_absolute(*args.click)
        display.roundtrip()
        time.sleep(0.1)
        fake.button(BUTTON_LEFT, 1)
        display.roundtrip()
        time.sleep(0.05)
        fake.button(BUTTON_LEFT, 0)
        display.roundtrip()

    codes = [KEYS[key] for key in args.keys]
    for code in codes:
        fake.keyboard_key(code, 1)
        display.roundtrip()
        time.sleep(0.02)
    time.sleep(args.hold)
    for code in reversed(codes):
        fake.keyboard_key(code, 0)
        display.roundtrip()
        time.sleep(0.02)
    display.disconnect()


if __name__ == '__main__':
    main()
