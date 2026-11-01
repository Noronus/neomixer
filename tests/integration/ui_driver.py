'''
Runs the real app inside the sandbox and scripts it: a scenario is a generator that drives the
app in-process, and yields how many seconds to let it run.

    source .sandbox/env
    python tests/integration/ui_driver.py <scenario> [output dir]
'''
import os
import sys
import json
import shutil
import signal
import subprocess
import time
import traceback

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib  # noqa: E402

from neomixer.scripts import pmctl  # noqa: E402
from neomixer.clients.gtk.gtk_client import GtkClient  # noqa: E402
from neomixer import settings  # noqa: E402


def screenshot(widget, path):
    '''Render a realized widget to a PNG.'''
    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    texture = widget.get_native().get_renderer().render_texture(node, None)
    texture.save_to_png(path)
    print(f'screenshot: {path} ({width}x{height})')


def create_null_sink(name, channels=2):
    positions = 'MONO' if channels == 1 else 'FL FR'
    subprocess.run(['pw-cli', 'create-node', 'adapter', (
        f'{{ factory.name=support.null-audio-sink node.name="{name}" node.description="{name}" '
        f'media.class=Audio/Sink audio.channels={channels} audio.position="{positions}" '
        'monitor.channel-volumes=true object.linger=true }')], stdout=subprocess.DEVNULL, check=True)


def create_null_source(name, channels=1):
    positions = 'MONO' if channels == 1 else 'FL FR'
    subprocess.run(['pw-cli', 'create-node', 'adapter', (
        f'{{ factory.name=support.null-audio-sink node.name="{name}" node.description="{name}" '
        f'media.class=Audio/Source/Virtual audio.channels={channels} audio.position="{positions}" '
        'object.linger=true }')], stdout=subprocess.DEVNULL, check=True)


def fake_hardware_of(config):
    '''Stand-ins for the hardware devices in a config.'''
    for device in config['devices'].get('a', {}).values():
        create_null_sink(device['name'], device['channels'])
    for device in config['devices'].get('hi', {}).values():
        create_null_source(device['name'], device['channels'])


class Player:
    '''A program playing a looping tone, like a stream of the given name.'''

    def __init__(self, name, binary=None, role=None, seconds=120, freq=440):
        self.name = name
        self.proc = None
        props = f'application.name="{name}" node.name="{name}"'
        if binary:
            props += f' application.process.binary="{binary}"'
        if role:
            props += f' media.role="{role}"'
        self.command = ['pw-play', '-P', f'{{ {props} }}', self.tone(freq, seconds)]

    @staticmethod
    def tone(freq, seconds):
        path = os.path.join(settings.XDG_RUNTIME_DIR, f'tone-{freq}-{seconds}.wav')
        if not os.path.exists(path):
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                            f'sine=frequency={freq}:duration={seconds}', '-ac', '2', '-ar', '48000', path], check=True)
        return path

    def start(self):
        self.proc = subprocess.Popen(self.command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self

    def stop(self):
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait()
            self.proc = None


def stream_device(app_name):
    '''The sink a program's stream currently plays into, None without a stream.'''
    listed = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sink-inputs'], text=True))
    sinks = {sink['index']: sink['name'] for sink in json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks'], text=True))}
    for stream in listed:
        if stream['properties'].get('application.name') == app_name:
            return sinks.get(stream['sink'])
    return None


def links():
    return subprocess.check_output(['pw-link', '-l', '-o'], text=True)


class Driver:

    def __init__(self, app, out_dir):
        self.app = app
        self.out_dir = out_dir
        self.failures = []
        self.players = []

    @property
    def ui(self):
        return self.app.gtk_controller

    @property
    def content(self):
        return self.app.gtk_controller.content

    def shot(self, name, widget=None):
        screenshot(widget or self.app.window, os.path.join(self.out_dir, f'{name}.png'))

    def play(self, *args, **kwargs):
        player = Player(*args, **kwargs).start()
        self.players.append(player)
        return player

    def card(self, key, app_type='sink_input'):
        return self.content.app_cards.get((app_type, key))

    def check(self, condition, message):
        print(('ok    ' if condition else 'FAIL  ') + message)
        if not condition:
            self.failures.append(message)

    def stop_players(self):
        for player in self.players:
            player.stop()


def run(scenario, out_dir, start_hidden=False):
    os.makedirs(out_dir, exist_ok=True)
    app = GtkClient(start_hidden=start_hidden)
    driver = Driver(app, out_dir)

    def begin():
        steps = scenario(driver)

        def advance():
            try:
                delay = next(steps)
            except StopIteration:
                app.quit()
                return False
            except Exception:
                traceback.print_exc()
                driver.failures.append('scenario raised')
                app.quit()
                return False
            GLib.timeout_add(int(delay * 1000), advance)
            return False

        advance()
        return False

    GLib.timeout_add(600, begin)
    app.run(None)
    driver.stop_players()
    return driver.failures


if __name__ == '__main__':
    import importlib
    sys.path.insert(0, os.path.dirname(__file__))
    scenarios = importlib.import_module('tests.integration.ui_scenarios')
    name = sys.argv[1]
    failures = run(getattr(scenarios, name), sys.argv[2] if len(sys.argv) > 2 else '.', start_hidden=name == 'starts_hidden')
    print(f'{len(failures)} failure(s)')
    for failure in failures:
        print('  ', failure)
    sys.exit(1 if failures else 0)
