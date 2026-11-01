'''
Scripted runs of the real app, see ui_driver.py.
'''

HEADSET = 'alsa_output.test_headset.analog-stereo'
FRAME = 'vrlink-sink'
MIC_NAME = 'alsa_input.test_headset.mono-fallback'


def first_look(d):
    '''The window with a few programs playing.'''
    d.play('Firefox', binary='firefox', freq=300)
    d.play('Cyberpunk 2077', binary='Cyberpunk2077.exe', freq=500)
    yield 1.5
    d.shot('first_look')


def first_run(d):
    '''Someone new: no config, nothing to import, an empty mixer and the welcome window.'''
    import subprocess
    yield 1.0
    d.check(d.app.config_persistence.first_run, 'knows it is the first run')
    d.check(d.ui.welcome_window is not None, 'says hello')
    d.check(not any(d.content.device_box[kind].widgets for kind in ('vi', 'hi', 'a', 'b')), 'starts empty')
    d.shot('first_run')
    d.shot('first_run_welcome', d.ui.welcome_window)
    d.ui.welcome_window.close()

    popover = d.content.create_device_button['vi'].get_popover()
    popover.nick_widget.set_option('first')
    popover.name_widget.set_option('firstinput')
    popover.combobox_widget.set_active_name('Stereo')
    d.content._on_device_create(None, popover)
    yield 0.8
    sinks = subprocess.check_output(['pactl', 'list', 'short', 'sinks'], text=True)
    d.check('firstinput' in sinks, 'the first virtual input appears in the sound server')
    d.shot('first_run_device')

    # Back out of the way of the rest of the suite
    d.ui.device_remove(strip(d, 'vi', 'first'), 'vi', next(iter(d.content.device_box['vi'].widgets)))
    yield 0.5
    d.check('firstinput' not in subprocess.check_output(['pactl', 'list', 'short', 'sinks'], text=True), 'and is gone again')


def demo_screenshot(d):
    '''A busy mixer for the README.'''
    d.app.app_registry.add_manually('sink_input', 'spotify', 'Spotify', None, 'spotify')
    d.app.app_registry.set_device('sink_input', 'spotify', 'inmedia')
    d.app.app_registry.add_manually('sink_input', 'vlc', 'VLC media player', 'vlc', 'vlc')
    d.app.app_registry.add_manually('sink_input', 'steam', 'Steam', 'steam', 'steam')
    d.play('Firefox', binary='firefox', freq=300)
    d.play('Cyberpunk 2077', binary='Cyberpunk2077.exe', freq=500)
    d.play('Discord', binary='Discord', freq=700)
    yield 1.0
    select_device(d.card('firefox'), 'inmedia')
    select_device(d.card('cyberpunk2077.exe'), 'in1')
    select_device(d.card('discord'), 'indiscord')
    strip(d, 'vi', 'in media').device_model.eq.apply_preset('Loudness')
    strip(d, 'vi', 'in media').device_model.eq.enabled = True
    d.ui._on_eq_window_changed(None, 'vi', '2')
    strip(d, 'vi', 'in1').volume_widget.set_value(85)
    strip(d, 'vi', 'in discord').volume_widget.set_value(92)
    yield 2.0
    d.shot('neomixer')
    d.ui.open_eq_window(None, 'vi', '2')
    yield 0.6
    d.shot('neomixer-eq', d.ui.eq_windows[('vi', '2')])
    d.stop_players()


def crowded(d):
    '''Many programs must not disturb the mixer, nor make the window scroll sideways.'''
    d.play('Firefox', binary='firefox', freq=300)
    yield 1.0
    strip = d.content.device_box['vi'].widgets['1']
    width, height = d.app.window.get_width(), d.app.window.get_height()
    strip_size = strip.get_width(), strip.get_height()
    hadjustment = d.content.mixer_grid.get_parent().get_hadjustment()

    for number in range(40):
        d.app.app_registry.add_manually('sink_input', f'game{number}', f'Some Game {number}')
    for number in range(8):
        d.play(f'Stream {number}', binary=f'stream{number}', freq=200 + 50 * number)
    yield 1.5
    d.shot('crowded')

    d.check(d.app.window.get_width() == width and d.app.window.get_height() == height, 'window keeps its size')
    d.check((strip.get_width(), strip.get_height()) == strip_size, f'strips keep their size {strip_size}')
    d.check(hadjustment.get_upper() <= hadjustment.get_page_size(), 'mixer does not scroll sideways')
    d.check(len(d.content.app_cards) >= 49, f'all listed: {len(d.content.app_cards)}')

    d.app.window.set_default_size(1100, 700)
    yield 1.0
    d.shot('crowded_small')
    d.check(d.app.window.get_width() <= 1100, f'window can get small: {d.app.window.get_width()}')
    d.check(hadjustment.get_upper() <= hadjustment.get_page_size() + 1, 'small mixer does not scroll sideways')

    # leave no trace in the saved config
    d.stop_players()
    yield 0.5
    for key in list(d.app.app_registry.entries['sink_input']):
        if key.startswith(('game', 'stream')):
            d.app.app_registry.forget('sink_input', key)
    d.app.window.set_default_size(width, height)
    yield 0.2


#
# # Behaviour, these run one after the other in the same sandbox: see lifecycle_test.py
#
def select_device(card, device_name):
    '''Choose a device in a program's selector, the way a click does.'''
    data = card.combobox._device_data[card.app_type]
    for index, entry in enumerate(data):
        if entry[1] == device_name:
            card.combobox.set_selected(index)
            return
    raise ValueError(device_name)


def strip(d, device_type, nick):
    for widget in d.content.device_box[device_type].widgets.values():
        if widget.device_model.nick == nick:
            return widget
    raise ValueError(nick)


def assign_and_restart_program(d):
    from tests.integration.ui_driver import stream_device
    d.check(d.card('firefox') is None, 'no program listed yet')
    player = d.play('Firefox', binary='firefox', freq=330)
    yield 1.0
    card = d.card('firefox')
    d.check(card is not None and card.entry.state == 'active', 'playing program gets listed')
    d.check(stream_device('Firefox') != 'inmedia', f'starts elsewhere: {stream_device("Firefox")}')

    select_device(card, 'inmedia')
    yield 0.5
    d.check(stream_device('Firefox') == 'inmedia', f'choice moves it: {stream_device("Firefox")}')
    d.check(card.entry.known.device == 'inmedia', 'choice is remembered')

    card.volume_widget.set_value(55)
    yield 0.3
    d.check(card.entry.known.volume == 55, f'volume is remembered: {card.entry.known.volume}')

    player.stop()
    yield 0.6
    d.check(card.entry.state == 'offline', 'program stays listed after it stops')
    d.shot('program_stopped')

    d.play('Firefox', binary='firefox', freq=330)
    yield 0.8
    d.check(stream_device('Firefox') == 'inmedia', f'restarted program goes back: {stream_device("Firefox")}')
    d.check(card.entry.state == 'active', 'same entry is active again')
    d.check(len(d.content.app_cards) == 1, f'no second entry: {list(d.content.app_cards)}')


def setup_while_offline(d):
    '''Two programs that do not run: one known, one added by hand.'''
    from tests.integration.ui_driver import stream_device
    d.app.app_registry.add_manually('sink_input', 'spotify', 'Spotify', None, 'spotify')
    yield 0.3
    card = d.card('spotify')
    d.check(card is not None and card.entry.state == 'offline', 'program can be added before it ever ran')
    select_device(card, 'indiscord')
    yield 0.2
    d.play('Spotify', binary='spotify', freq=350)
    yield 0.8
    d.check(stream_device('Spotify') == 'indiscord', f'first start goes to the choice: {stream_device("Spotify")}')
    d.stop_players()
    yield 0.3


def idle_client_is_listed(d):
    '''A program is listed from the moment it connects, not only once it plays.'''
    import subprocess
    import sys
    import pulsectl

    # own connections are not programs
    own = pulsectl.Pulse('Own Connection')
    yield 0.5
    d.check(d.card('own connection') is None, 'own connections are not listed')
    own.close()

    idle = subprocess.Popen([
        sys.executable, '-c',
        "import pulsectl, time; client = pulsectl.Pulse('Idle Program'); time.sleep(60)"
    ])
    yield 1.5
    card = d.card('idle program')
    d.check(card is not None and card.entry.state == 'idle', 'connected program is listed without a stream')
    d.check(card is not None and 'app-idle' in card.get_css_classes(), 'and looks connected')
    d.shot('idle_program')

    idle.terminate()
    idle.wait()
    yield 0.8
    d.check(card.entry.state == 'offline', 'it stays listed after disconnecting')
    d.check(card.forget_button.get_visible(), 'and can be forgotten now')
    card.forget_button.emit('clicked')
    yield 0.2
    d.check(d.card('idle program') is None, 'forgetting removes it')


def set_everything_then_get_killed(d):
    '''Change settings, and die without any chance to save: as in a power cut.'''
    import os
    import signal

    media = strip(d, 'vi', 'in media')
    media.volume_widget.set_value(70)
    strip(d, 'vi', 'in1').mute_widget.set_active(True)
    speakers = media.connections_widgets['a'].widgets['2']
    speakers.toggle_button.set_active(False)
    yield 0.3

    # The EQ on, with an audible curve
    media.emit('eq_pressed')
    yield 0.8
    window = d.ui.eq_windows[('vi', '2')]
    window.eq.apply_preset('Bass Boost')
    window.enable_switch.set_active(True)
    yield 1.0
    d.shot('eq_window', window)
    d.shot('eq_on_strip')
    yield 3.0  # past the autosave
    os.kill(os.getpid(), signal.SIGKILL)
    yield 1


def all_restored_after_reboot(d):
    '''What the reboot took away has to be back, without touching anything.'''
    from neomixer.scripts import pmctl
    from tests.integration.ui_driver import stream_device, links

    media = strip(d, 'vi', 'in media')
    d.check(media.device_model.volume[0] == 70, f'saved volume survived a kill: {media.device_model.volume}')
    pa_media = pmctl.get_device_by_name('sink', 'inmedia')
    d.check(pa_media is not None, 'virtual device is back')
    d.check(round(pa_media.volume.values[0] * 100) == 70, f'and has its volume: {pa_media.volume.values}')
    in1 = pmctl.get_device_by_name('sink', 'in1')
    d.check(bool(in1.mute), 'and mute')
    d.check(media.device_model.eq.enabled and media.device_model.eq.preset == 'Bass Boost', 'EQ settings are back')
    d.check('eq-on' in media.eq_button.get_css_classes(), 'EQ button shows it is on')
    d.check(media.connections_widgets['a'].widgets['2'].toggle_button.get_active() is False, 'route switched off stays off')

    linked = links()
    d.check('inmedia:monitor_FL' in linked and 'input.nm_eq_inmedia:input_FL' in linked, 'EQ is fed')
    d.check(f'output.nm_eq_inmedia:output_FL\n  |-> {HEADSET}:playback_FL' in linked, 'EQ plays into the route')
    d.check('indiscord:monitor_FL' in linked, 'other routes are linked')

    card = d.card('firefox')
    d.check(card is not None and card.entry.state == 'offline', 'program is listed though it did not start yet')
    d.check(card.entry.device == 'inmedia', 'with its output')
    d.check(card.volume_widget.get_volume() == 55, f'and volume {card.volume_widget.get_volume()}')
    yield 0.5  # a slower machine has not drawn the window yet
    d.shot('after_reboot')

    d.play('Firefox', binary='firefox', freq=330)
    yield 0.8
    d.check(stream_device('Firefox') == 'inmedia', f'program goes where it belongs: {stream_device("Firefox")}')
    stream = next(iter(card.entry.streams.values()))
    pa_stream = pmctl.app_by_id(stream.index, 'sink_input')
    d.check(round(pa_stream.volume.values[0] * 100) == 55, f'at its volume: {pa_stream.volume.values}')
    d.stop_players()
    yield 0.3


def eq_changes_the_sound(d):
    '''Through the whole app: a tone into the input, measured at the hardware output.'''
    from tests.integration.audio_probe import measure_db
    output = HEADSET
    media = strip(d, 'vi', 'in media')
    device = media.device_model
    media.volume_widget.set_value(100)
    d.ui.open_eq_window(None, 'vi', '2')
    yield 0.5
    window = d.ui.eq_windows[('vi', '2')]
    window.eq.reset()
    window.eq.enabled = True
    window._edited()
    yield 0.5

    flat = measure_db('inmedia', output, 1000)
    d.check(flat is not None and abs(flat) < 0.5, f'flat EQ changes nothing: {flat}')

    window.selected = 3
    window.refresh()
    window.freq_spin.set_value(1000)
    window.gain_spin.set_value(9)
    yield 0.3
    boosted = measure_db('inmedia', output, 1000)
    d.check(boosted is not None and abs(boosted - 9) < 0.7, f'+9 dB band gives {boosted}')
    d.shot('eq_boosted', window)

    # dragging on the curve goes through the same path
    curve = window.curve
    x, y = curve.handle_position(window.eq.bands[3], curve.get_width(), curve.get_height())
    curve._on_drag_begin(None, x, y)
    curve._on_drag_update(None, 0, curve._gain_to_y(-6, curve.get_height()) - y)
    curve._on_drag_end()
    yield 0.3
    d.check(abs(window.eq.bands[3].gain + 6) < 0.2, f'drag set the gain: {window.eq.bands[3].gain}')
    dragged = measure_db('inmedia', output, 1000)
    d.check(dragged is not None and abs(dragged + 6) < 0.7, f'dragged to -6 dB gives {dragged}')

    # comparing: instant, no process restart
    from neomixer.scripts import pw_eq
    pids = pw_eq.running_pids('inmedia')
    media.emit('eq_toggled')
    yield 0.3
    bypassed = measure_db('inmedia', output, 1000)
    d.check(bypassed is not None and abs(bypassed) < 0.5, f'EQ off passes through: {bypassed}')
    d.check('eq-on' not in media.eq_button.get_css_classes(), 'button shows it is off')
    d.check(window.enable_switch.get_active() is False, 'window shows it is off')
    d.check(pw_eq.running_pids('inmedia') == pids, 'same process')
    media.emit('eq_toggled')
    yield 0.3

    # a filter type change has to rebuild it, the audio has to come back
    window.type_dropdown.set_selected(4)  # high pass
    yield 1.0
    d.check(pw_eq.running_pids('inmedia') != pids, 'type change rebuilds the filters')
    cut = measure_db('inmedia', output, 200)
    d.check(cut is not None and cut < -8, f'high pass cuts: {cut}')
    window.eq.reset()
    window._edited()
    yield 1.0
    restored = measure_db('inmedia', output, 200)
    d.check(restored is not None and abs(restored) < 0.5, f'reset restores: {restored}')

    # mono hardware input has an EQ too
    mic = strip(d, 'hi', 'mic')
    mic.device_model.eq.enabled = True
    mic.device_model.eq.bands[3].gain = 6
    d.ui._on_eq_window_changed(None, 'hi', '1')
    yield 1.0
    linked = __import__('tests.integration.ui_driver', fromlist=['links']).links()
    d.check('input.nm_eq_alsa_input.test_headset.mono-fallback:input_MONO' in linked, 'mono input EQ is fed')
    d.check('output.nm_eq_alsa_input.test_headset.mono-fallback:output_MONO\n  |-> mic1:input_MONO' in linked,
            'and plays into its route')
    mic.device_model.eq.enabled = False
    d.ui._on_eq_window_changed(None, 'hi', '1')
    window.eq.enabled = False
    window._edited()
    yield 0.3


def settings_apply(d):
    from neomixer.utils import autostart
    box = d.content.settings_box
    d.ui.settings_menu_open(None)
    d.check(not autostart.is_enabled(), 'autostart is off')
    box.autostart.set_active(True)
    yield 0.2
    d.check(autostart.is_enabled(), 'switch writes the autostart entry')
    with open(autostart.autostart_file(), encoding='utf-8') as entry:
        text = entry.read()
    print(text)
    d.check('--hidden' in text, 'starts hidden by default')
    box.start_hidden.set_active(False)
    yield 0.2
    with open(autostart.autostart_file(), encoding='utf-8') as entry:
        d.check('--hidden' not in entry.read(), 'can start with the window')
    box.autostart.set_active(False)
    yield 0.2
    d.check(not autostart.is_enabled(), 'switch removes the autostart entry')

    popover = d.content.settings_button.get_popover()
    popover.popup()
    yield 0.3
    d.shot('settings', popover)
    popover.popdown()

    route = strip(d, 'vi', 'in media').connections_widgets['a'].widgets['1']
    route.popover.popup()
    route._on_pressed(None, None, None, None)
    yield 0.3
    d.shot('route_popover', route.popover)
    route.popover.popdown()
    yield 0.1

    d.ui.open_welcome_window()
    yield 0.4
    d.shot('welcome', d.ui.welcome_window)
    d.ui.welcome_window.close()
    yield 0.1


def hotplug(d):
    import subprocess
    from neomixer.scripts import pmctl
    from tests.integration.ui_driver import create_null_sink, links, stream_device

    name = 'bluez_output.80_99_E7_ED_23_2C.1'
    subprocess.run(['pw-cli', 'destroy', name], check=False, stdout=subprocess.DEVNULL)
    yield 0.8
    blue = d.content.device_box['a'].widgets['4']
    d.check(blue.device_model.present is False, 'unplugged output is marked')
    d.check('disconnected' in blue.get_css_classes(), 'and dimmed')
    d.shot('unplugged')

    # a program whose output is the unplugged device
    card = d.app.app_registry.add_manually('sink_input', 'vlc', 'VLC', None, 'vlc')
    d.app.app_registry.set_device('sink_input', 'vlc', name)
    d.play('VLC', binary='vlc', freq=370)
    yield 0.6
    d.check(stream_device('VLC') != name, f'program waits elsewhere: {stream_device("VLC")}')

    create_null_sink(name)
    yield 1.5
    d.check(blue.device_model.present is True, 'output is back')
    d.check(f'{name}:playback_FL' in links(), 'routes to it are relinked')
    d.check(stream_device('VLC') == name, f'program goes to it: {stream_device("VLC")}')
    d.stop_players()
    yield 0.3
    d.app.app_registry.forget('sink_input', 'vlc')
    yield 0.1


def server_restart(d):
    '''The sound server restarting at runtime is a small reboot.'''
    import os
    import subprocess
    from neomixer.scripts import pmctl
    from tests.integration.ui_driver import links, stream_device

    media = strip(d, 'vi', 'in media')
    media.device_model.eq.enabled = True
    d.ui._on_eq_window_changed(None, 'vi', '2')
    yield 1.0

    script = os.path.join(os.path.dirname(__file__), 'sandbox.sh')
    subprocess.run([script, 'audio-restart', os.environ['NEOMIXER_SANDBOX']], check=True)
    # hardware stand-ins are gone with it, like real devices would come back
    from tests.integration.ui_driver import fake_hardware_of
    fake_hardware_of({'devices': {
        kind: {key: {'name': device.name, 'channels': device.channels} for key, device in d.app.device_repository.get_devices_by_type(kind).items()}
        for kind in ('a', 'hi')
    }})
    yield 3.5

    d.check(not d.ui.server_lost and not d.ui.status_label.get_visible(), 'noticed the server is back')
    d.check(pmctl.get_device_by_name('sink', 'inmedia') is not None, 'virtual devices are back')
    linked = links()
    d.check('inmedia:monitor_FL' in linked, 'routes are back')
    d.check('input.nm_eq_inmedia:input_FL' in linked, 'EQ is back')

    d.play('Firefox', binary='firefox', freq=330)
    yield 1.0
    d.check(stream_device('Firefox') == 'inmedia', f'program routing works again: {stream_device("Firefox")}')
    d.stop_players()
    media.device_model.eq.enabled = False
    d.ui._on_eq_window_changed(None, 'vi', '2')
    yield 0.3


def close_keeps_running(d):
    '''Closing the window must not stop the routing, and starting again must bring it back.'''
    import subprocess
    import sys
    from tests.integration.ui_driver import stream_device

    window = d.app.window
    d.check(d.app.config_model.background is True, 'background mode on by default')
    d.check(d.app.tray is not None, 'has a tray icon object')
    window.close()
    yield 0.5
    d.check(window.get_visible() is False, 'window is hidden')
    d.check(not d.ui.vumeter_tasks['vi'], 'meters stop while hidden')

    d.play('Firefox', binary='firefox', freq=330)
    yield 0.8
    d.check(stream_device('Firefox') == 'inmedia', 'programs still go where they belong')
    d.stop_players()

    # a second start only opens the window of the first
    second = subprocess.Popen([sys.executable, '-m', 'neomixer.main'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    yield 2.0
    d.check(second.poll() == 0, f'second start exits: {second.poll()}')
    d.check(window.get_visible() is True, 'and opens the window')
    d.check(bool(d.ui.vumeter_tasks['vi']), 'meters run again')
    yield 0.2

    # the tray offers the same
    d.ui.toggle_window()
    yield 0.3
    d.check(window.get_visible() is False, 'tray can hide it')
    d.ui.toggle_window()
    yield 0.3
    d.check(window.get_visible() is True, 'and show it')


def _default(kind):
    import subprocess
    return subprocess.check_output(['pactl', f'get-default-{kind}'], text=True).strip()


def _take_default(kind, name):
    '''Change the default device from outside, what Steam writes when the Frame connects.'''
    import subprocess
    subprocess.run(['pw-metadata', '-n', 'default', '0', f'default.configured.audio.{kind}', f'{{ "name": "{name}" }}', 'Spa:String:JSON'],
                   stdout=subprocess.DEVNULL, check=True)


def default_is_held(d):
    '''Whatever changes the default device loses it again, while the mixer's own choice wins.'''
    import subprocess
    import time
    from neomixer.scripts import pmctl_async
    from tests.integration.ui_driver import create_null_sink, stream_device

    in1, media = strip(d, 'vi', 'in1'), strip(d, 'vi', 'in media')
    mic1 = strip(d, 'b', 'mic1')
    d.check(media.hold_widget.get_parent() is media.primary_widget.get_parent(), 'lock sits next to the star')
    d.check(strip(d, 'a', 'headset').hold_widget.get_parent() is None, 'hardware outputs have no lock')

    # Without a hold the mixer follows, as it did before
    media.primary_widget.set_active(True)
    yield 0.4
    d.check(_default('sink') == 'inmedia', 'star sets the default')
    _take_default('sink', FRAME)
    yield 0.5
    d.check(_default('sink') == FRAME, f'nothing holds it: {_default("sink")}')
    d.check(not media.primary_widget.get_active(), 'the star shows it left')

    # The lock makes it the default as well
    media.hold_widget.set_active(True)
    yield 0.4
    d.check(_default('sink') == 'inmedia', f'holding makes it the default: {_default("sink")}')
    d.check(media.primary_widget.get_active() and media.device_model.primary, 'and shows that')
    d.shot('hold_on')

    # A program following the default goes along and back, it is not pinned anywhere
    d.play('Follower', binary='follower', freq=500)
    yield 0.8
    d.check(stream_device('Follower') == 'inmedia', f'program on the default: {stream_device("Follower")}')
    card = d.card('follower')

    started = time.monotonic()
    _take_default('sink', FRAME)
    while _default('sink') != 'inmedia' and time.monotonic() - started < 3:
        yield 0.01
    taken_back = time.monotonic() - started
    print(f'taken back after {taken_back * 1000:.0f} ms')
    d.check(taken_back < 1, f'taken back quickly: {taken_back:.3f}s')
    yield 0.5
    d.check(_default('sink') == 'inmedia', f'stays back: {_default("sink")}')
    d.check(media.primary_widget.get_active() and media.hold_widget.get_active(), 'the star and lock did not move')
    d.check(media.device_model.primary and media.device_model.hold_primary, 'neither did the model')
    d.check(stream_device('Follower') == 'inmedia', f'the program is back: {stream_device("Follower")}')
    d.check(not card.entry.known.device, f'and did not get pinned: {card.entry.known.device}')

    # The other ways to change it
    subprocess.run(['pactl', 'set-default-sink', 'indiscord'], check=True)
    yield 0.5
    d.check(_default('sink') == 'inmedia', f'pactl loses too: {_default("sink")}')

    # A device that appears and takes the default right away, like the Frame's own
    create_null_sink('late_arrival')
    _take_default('sink', 'late_arrival')
    yield 0.5
    d.check(_default('sink') == 'inmedia', f'a new device loses too: {_default("sink")}')

    # A virtual output, on its own
    mic1.hold_widget.set_active(True)
    yield 0.4
    d.check(_default('source') == 'mic1', f'virtual output is held: {_default("source")}')
    _take_default('source', MIC_NAME)
    yield 0.5
    d.check(_default('source') == 'mic1', f'the default input comes back: {_default("source")}')
    d.check(_default('sink') == 'inmedia', 'the output side did not care')

    # Someone who will not give up does not make both of us spin
    calls = []
    original = pmctl_async.set_primary

    async def counted(*args):
        calls.append(time.monotonic())
        return await original(*args)

    pmctl_async.set_primary = counted
    insist = f'pw-metadata -n default 0 default.configured.audio.sink \'{{ "name": "{FRAME}" }}\' Spa:String:JSON >/dev/null'
    insisting = subprocess.Popen(['sh', '-c', f'for i in $(seq 100); do {insist}; sleep 0.02; done'])
    yield 2.0
    insisting.terminate()
    insisting.wait()
    pmctl_async.set_primary = original
    print(f'{len(calls)} times taken back in 2 s of insisting')
    d.check(1 <= len(calls) <= 6, f'at a bounded rate: {len(calls)}')
    yield 1.2
    d.check(_default('sink') == 'inmedia', f'and it wins in the end: {_default("sink")}')

    # The star is the mixer saying something else: the hold goes along
    in1.primary_widget.set_active(True)
    yield 0.4
    d.check(_default('sink') == 'in1', f'star moves the default: {_default("sink")}')
    d.check(in1.hold_widget.get_active() and not media.hold_widget.get_active(), 'the lock moves along')
    d.check(not media.primary_widget.get_active(), 'star left the other strip')
    _take_default('sink', FRAME)
    yield 0.5
    d.check(_default('sink') == 'in1', f'the new one is held: {_default("sink")}')

    # And so does a lock on another strip
    media.hold_widget.set_active(True)
    yield 0.4
    d.check(_default('sink') == 'inmedia' and media.primary_widget.get_active(), 'lock on another strip takes over')
    d.check(not in1.hold_widget.get_active() and not in1.primary_widget.get_active(), 'the first lets go')

    # Clicking the star of the default does not fake it being off
    media.primary_widget.set_active(False)
    yield 0.2
    d.check(media.primary_widget.get_active(), 'a star cannot be turned off')

    # Editing a device recreates it
    media.popover.confirm_button.emit('clicked')
    yield 1.0
    d.check(strip(d, 'vi', 'in media').device_model.hold_primary, 'editing the device keeps the hold')
    d.check(_default('sink') == 'inmedia', f'and it is the default again: {_default("sink")}')

    # Off again: changes stick
    media = strip(d, 'vi', 'in media')
    media.hold_widget.set_active(False)
    yield 0.2
    _take_default('sink', FRAME)
    yield 0.5
    d.check(_default('sink') == FRAME, f'no hold, no switching back: {_default("sink")}')
    d.check(not media.primary_widget.get_active(), 'the star follows again')

    subprocess.run(['pw-cli', 'destroy', 'late_arrival'], check=False, stdout=subprocess.DEVNULL)
    mic1.hold_widget.set_active(False)
    yield 0.2
    d.stop_players()


def hold_then_killed_and_default_taken(d):
    '''The next one starts while something else is the default.'''
    import os
    import signal
    import subprocess
    media, mic1 = strip(d, 'vi', 'in media'), strip(d, 'b', 'mic1')
    media.hold_widget.set_active(True)
    mic1.hold_widget.set_active(True)
    yield 2.5  # the autosave
    d.check(_default('sink') == 'inmedia', 'held')
    insist = f'pw-metadata -n default 0 default.configured.audio.sink \'{{ "name": "{FRAME}" }}\' Spa:String:JSON >/dev/null'
    subprocess.Popen(['sh', '-c', f'sleep 0.2; {insist}'], start_new_session=True)
    os.kill(os.getpid(), signal.SIGKILL)


def hold_wins_at_start(d):
    media = strip(d, 'vi', 'in media')
    yield 0.8
    d.check(media.hold_widget.get_active(), 'the lock is saved')
    d.check(_default('sink') == 'inmedia', f'changed while it was off, taken back at start: {_default("sink")}')
    d.check(media.primary_widget.get_active(), 'star is there')
    d.check(strip(d, 'b', 'mic1').hold_widget.get_active(), 'the lock on the virtual output too')


def hold_then_killed(d):
    '''What WirePlumber remembers across a reboot is what the other program set.'''
    import os
    import signal
    yield 0.3
    strip(d, 'vi', 'in media').hold_widget.set_active(False)
    yield 0.2
    _take_default('sink', FRAME)
    yield 0.4
    d.check(_default('sink') == FRAME, 'the session manager saves this one')
    strip(d, 'vi', 'in media').hold_widget.set_active(True)
    yield 2.5  # the autosave
    os.kill(os.getpid(), signal.SIGKILL)


def hold_after_reboot(d):
    media = strip(d, 'vi', 'in media')
    yield 1.0
    d.check(media.hold_widget.get_active(), 'hold survives a reboot')
    d.check(_default('sink') == 'inmedia', f'it is the default after a reboot: {_default("sink")}')
    _take_default('sink', FRAME)
    yield 0.5
    d.check(_default('sink') == 'inmedia', f'and still held: {_default("sink")}')
    d.shot('hold_after_reboot')

    # The rest of the suite works with what it had
    media.hold_widget.set_active(False)
    strip(d, 'b', 'mic1').hold_widget.set_active(False)
    strip(d, 'vi', 'in1').primary_widget.set_active(True)
    yield 2.5


def _labels(widget):
    '''Every label in a widget tree, popovers included.'''
    from gi.repository import Gtk
    if isinstance(widget, Gtk.Label):
        yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _labels(child)
        child = child.get_next_sibling()


def _font_px(widget):
    from gi.repository import Pango
    description = widget.get_pango_context().get_font_description()
    size = description.get_size() / Pango.SCALE
    return size if description.get_size_is_absolute() else size * 96 / 72


def text_size_setting(d):
    '''The setting has to reach all text, where themes reset fonts too.'''
    from neomixer.clients.gtk import text_size

    window = d.app.window
    d.check(d.app.config_model.text_size == 9.0, 'default text size')
    # the headless monitors report no size: the fallback
    expected = text_size.text_px(9, None)
    d.check(d.ui.text_scaler.px_of(window) == round(expected, 2), f'window at {expected}: {d.ui.text_scaler.px_of(window)}')

    # em sized labels are smaller by design, these are at full size
    nick = strip(d, 'vi', 'in media').nick_label
    d.check(abs(_font_px(nick) - expected) < 0.2, f'strip name: {_font_px(nick)}')
    settings_label = d.content.settings_box.get_first_child().get_first_child()

    d.ui.settings_menu_open(None)
    box = d.content.settings_box
    d.check(box.text_size.get_value() == 9.0, 'settings show it')
    box.text_size.set_value(14)
    yield 0.3
    big = text_size.text_px(14, None)
    d.check(d.app.config_model.text_size == 14, 'setting is saved')
    d.check(abs(_font_px(nick) - big) < 0.2, f'strip name grows: {_font_px(nick)}')
    d.check(d.content.text_factor > 1.3, f'cards grow along: {d.content.text_factor}')

    # all text, nothing left at the theme's size
    route = strip(d, 'vi', 'in media').connections_widgets['a'].widgets['1']
    too_small = [label.get_label() for label in _labels(d.app.window)
                 if _font_px(label) < big * 0.6]
    d.check(not too_small, f'nothing kept the old size: {too_small[:5]}')
    route.popover.popup()
    route._on_pressed(None, None, None, None)
    yield 0.2
    d.shot('text_big_route', route.popover)
    route.popover.popdown()

    d.ui.open_eq_window(None, 'vi', '2')
    yield 0.5
    eq = d.ui.eq_windows[('vi', '2')]
    d.check(abs(eq.curve.text_factor - d.content.text_factor) < 0.01, 'curve labels grow')
    too_small = [label.get_label() for label in _labels(eq) if _font_px(label) < big * 0.6]
    d.check(not too_small, f'nothing in the EQ window kept the old size: {too_small[:5]}')
    d.shot('text_big_eq', eq)
    eq.close()
    d.shot('text_big')

    # popovers get their style once they open
    popover = d.content.settings_button.get_popover()
    popover.popup()
    yield 0.3
    d.check(abs(_font_px(settings_label) - big) < 0.2, f'popover grows: {_font_px(settings_label)}')
    d.shot('text_big_settings', popover)
    popover.popdown()

    box.text_size.set_value(6.5)
    yield 0.4
    d.shot('text_small')
    box.text_size.set_value(9)
    yield 0.3
    d.check(abs(_font_px(nick) - expected) < 0.2, f'and back: {_font_px(nick)}')


def text_follows_the_monitor(d):
    '''Moved to a monitor of another resolution, a window keeps the physical size of its text.'''
    import os
    import subprocess
    import tempfile
    from gi.repository import Gdk
    from neomixer.clients.gtk import text_size

    monitors = Gdk.Display.get_default().get_monitors()
    d.check(monitors.get_n_items() == 2, f'two monitors: {monitors.get_n_items()}')
    connectors = [monitors.get_item(index).get_connector() for index in range(monitors.get_n_items())]

    # the headless monitors have no size, so give them the ones of a 27" 1440p and a 24" 1200p
    dpis = dict(zip(connectors, (108.9, 94.1)))
    text_size.monitor_dpi = lambda monitor: dpis[monitor.get_connector()]
    d.ui.text_scaler.update()
    yield 0.2

    def move_to(index):
        script = (
            'var w = workspace.windowList().filter(function (c) { return c.caption == "NeoMixer"; })[0];'
            f'workspace.sendClientToScreen(w, workspace.screens[{index}]);'
        )
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as out:
            out.write(script)
        bus = ['gdbus', 'call', '--session', '--dest', 'org.kde.KWin']
        loaded = subprocess.run(
            [*bus, '--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.loadScript', out.name, f'move{index}'],
            capture_output=True, text=True, check=False
        ).stdout
        script_id = loaded.strip().strip('(),').split()[-1]
        subprocess.run(
            [*bus, '--object-path', f'/Scripting/Script{script_id}', '--method', 'org.kde.kwin.Script.run'],
            capture_output=True, check=False
        )
        subprocess.run(
            [*bus, '--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.unloadScript', f'move{index}'],
            capture_output=True, check=False
        )
        os.unlink(out.name)

    window = d.app.window
    on_monitor = lambda: Gdk.Display.get_default().get_monitor_at_surface(window.get_surface()).get_connector()
    start = on_monitor()
    other = next(index for index, connector in enumerate(connectors) if connector != start)
    expected_here = round(text_size.text_px(9, dpis[start]), 2)
    d.check(d.ui.text_scaler.px_of(window) == expected_here, f'on {start}: {d.ui.text_scaler.px_of(window)}')

    move_to(other)
    yield 1.0
    d.check(on_monitor() == connectors[other], f'window moved: {on_monitor()}')
    expected_there = round(text_size.text_px(9, dpis[connectors[other]]), 2)
    d.check(d.ui.text_scaler.px_of(window) == expected_there, f'text follows: {d.ui.text_scaler.px_of(window)}, wanted {expected_there}')
    nick = strip(d, 'vi', 'in media').nick_label
    d.check(abs(_font_px(nick) - expected_there) < 0.2, f'and is drawn so: {_font_px(nick)}')

    # hidden and shown again, as the tray does
    d.ui.hide_window()
    yield 0.3
    d.ui.show_window()
    yield 0.6
    d.check(window.get_visible(), 'shows again')
    d.check(d.ui.text_scaler.px_of(window) in (expected_here, expected_there), f'still sized: {d.ui.text_scaler.px_of(window)}')
    move_to(0 if connectors[0] == start else 1)
    yield 0.6


def _measure_at_device(device, play_args, freq=1000, seconds=0.9):
    '''What really reaches a device, with a recorder linked by hand to its monitor ports.'''
    import math
    import os
    import signal
    import subprocess
    import tempfile
    import time
    from neomixer.scripts import pmctl
    from tests.integration.audio_probe import _tone_magnitude, _write_sine, AMPLITUDE, UNTOUCHED

    with tempfile.TemporaryDirectory() as folder:
        tone, recording = os.path.join(folder, 'tone.wav'), os.path.join(folder, 'out.wav')
        _write_sine(tone, freq, seconds + 1.5)
        recorder = subprocess.Popen(
            ['pw-record', '-P', '{ node.name=nmprobe node.autoconnect=false }', '--rate', '48000', '--channels', '2',
             '--format', 's16', recording], stderr=subprocess.DEVNULL
        )
        time.sleep(0.25)
        for channel in ('FL', 'FR'):
            pmctl.link(f'{device}:monitor_{channel}', f'nmprobe:input_{channel}')
        player = subprocess.Popen(['pw-play', *UNTOUCHED, *play_args, tone], stderr=subprocess.DEVNULL)
        time.sleep(0.45 + seconds)
        recorder.send_signal(signal.SIGINT)
        recorder.wait()
        player.terminate()
        player.wait()
        magnitude = _tone_magnitude(recording, freq, 0.55)
    return None if magnitude < 1e-5 else 20 * math.log10(magnitude / AMPLITUDE)


def _set_band(d, device_type, device_id, gain):
    d.ui.open_eq_window(None, device_type, device_id)
    window = d.ui.eq_windows[(device_type, device_id)]
    window.eq.reset()
    window.eq.enabled = True
    window.selected = 3
    window.eq.bands[3].freq = 1000
    window.eq.bands[3].gain = gain
    window._edited()
    return window


def output_eq(d):
    '''EQ on an output: routed inputs and programs playing straight into it all go through.'''
    import subprocess
    from neomixer.scripts import pmctl, pw_eq
    from tests.integration.ui_driver import create_null_sink, links, stream_device

    media = strip(d, 'vi', 'in media')
    media.volume_widget.set_value(100)
    headset = strip(d, 'a', 'headset')
    d.check(headset.eq_button.get_parent() is not None, 'hardware outputs have an EQ button')
    d.check(strip(d, 'b', 'mic1').eq_button.get_parent() is not None, 'virtual outputs too')
    yield 0.2

    before = _measure_at_device(HEADSET, ['--target', 'inmedia'])
    direct_before = _measure_at_device(HEADSET, ['--target', HEADSET])
    d.check(before is not None and abs(before) < 0.5, f'route without EQ is flat: {before}')

    window = _set_band(d, 'a', '1', 9)
    yield 1.2
    d.check(bool(pw_eq.running_pids(HEADSET)), 'EQ runs')
    linked = links()
    d.check(('inmedia:monitor_FL', f'input.nm_eq_{HEADSET}:playback_FL') in pmctl.get_links('inmedia'), 'routes play into it')
    d.check(f'output.nm_eq_{HEADSET}:output_FL\n  |-> {HEADSET}:playback_FL' in linked, 'the session manager links it to the device')

    routed = _measure_at_device(HEADSET, ['--target', 'inmedia'])
    d.check(routed is not None and abs(routed - 9) < 0.7, f'routed input gets +9 dB: {routed}')
    direct = _measure_at_device(HEADSET, ['--target', HEADSET])
    d.check(direct is not None and abs(direct - direct_before - 9) < 0.7, f'program on the output gets +9 dB: {direct}')

    subprocess.run(['pactl', 'set-default-sink', HEADSET], check=False)
    yield 0.3
    default = _measure_at_device(HEADSET, [])
    d.check(default is not None and abs(default - 9) < 0.7, f'program following the default gets +9 dB: {default}')
    d.check(subprocess.check_output(['pactl', 'get-default-sink'], text=True).strip() == HEADSET, 'default device stays the device')

    # what the app shows
    d.check(not [device.name for device in d.ui.list_devices('a') if 'nm_eq_' in device.name], 'the EQ is not offered as a device')
    player = d.play('Firefox', binary='firefox', freq=330)
    card = d.card('firefox')
    select_device(card, HEADSET)
    yield 0.8
    d.check(stream_device('Firefox') in (HEADSET, f'input.nm_eq_{HEADSET}'), f'program goes to it: {stream_device("Firefox")}')
    d.check(card.entry.device == HEADSET, f'and shows the output, not its EQ: {card.entry.device}')
    d.check(list(card.entry.streams.values())[0].device == HEADSET, 'also on the stream')
    yield 2.5  # past the time a stream is being placed
    d.check(card.entry.known.device == HEADSET, 'the choice stays')
    select_device(card, 'inmedia')
    yield 0.5
    player.stop()

    # comparing
    window.enable_switch.set_active(False)
    yield 0.3
    off = _measure_at_device(HEADSET, ['--target', 'inmedia'])
    d.check(off is not None and abs(off) < 0.5, f'off passes through: {off}')
    window.enable_switch.set_active(True)
    yield 0.3

    # with an input's EQ in the same route, both count
    input_window = _set_band(d, 'vi', '2', 6)
    yield 1.0
    both = _measure_at_device(HEADSET, ['--target', 'inmedia'])
    d.check(both is not None and abs(both - 15) < 0.7, f'input and output EQ add up to +15 dB: {both}')
    input_window.eq.reset()
    input_window.eq.enabled = False
    input_window._edited()
    yield 0.4

    # unplugged the EQ must not make routed audio come out of another device
    other = 'alsa_output.test_card.analog-stereo'
    subprocess.run(['pw-cli', 'destroy', HEADSET], check=False, stdout=subprocess.DEVNULL)
    yield 1.0
    d.check(headset.device_model.present is False, 'output is unplugged')
    d.check(f'output.nm_eq_{HEADSET}' not in links(), 'its EQ is gone with it')
    d.check(not pw_eq.running_pids(HEADSET), 'no process is left')

    create_null_sink(HEADSET)
    yield 1.5
    d.check(headset.device_model.present is True, 'output is back')
    back = _measure_at_device(HEADSET, ['--target', 'inmedia'])
    d.check(back is not None and abs(back - 9) < 0.7, f'with its EQ: {back}')

    # virtual output: only routes reach it, recorded by a program
    bus_window = _set_band(d, 'b', '1', 9)
    yield 1.0
    linked = links()
    d.check('output.nm_eq_mic1:output_MONO\n  |-> mic1:input_MONO' in linked, 'virtual output has its EQ linked')
    d.check('input.nm_eq_mic1:input_MONO' in linked, 'and routes play into it')
    d.shot('eq_on_outputs')

    # switched off for the next runs
    for key in (('a', '1'), ('b', '1')):
        eq_window = d.ui.eq_windows[key]
        eq_window.eq.reset()
        eq_window.eq.enabled = False
        eq_window._edited()
    yield 0.3


def tray_is_on_the_bus(d):
    '''Talks to the tray icon the way a desktop panel does.'''
    import subprocess

    def gdbus(path, method, *args):
        # the app has to keep running to answer, so this can't wait here
        return subprocess.Popen(
            ['gdbus', 'call', '--session', '--dest', d.app.tray.bus_name, '--object-path', path, '--method', method, '--', *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

    item, menu = '/StatusNotifierItem', '/StatusNotifierItem/Menu'
    get = 'org.freedesktop.DBus.Properties.Get'
    title = gdbus(item, get, 'org.kde.StatusNotifierItem', 'Title')
    icon = gdbus(item, get, 'org.kde.StatusNotifierItem', 'IconName')
    layout = gdbus(menu, 'com.canonical.dbusmenu.GetLayout', '0', '-1', '[]')
    yield 0.5
    title, icon, layout = (call.communicate()[0].strip() for call in (title, icon, layout))
    d.check('NeoMixer' in title, f'title: {title}')
    d.check('org.neomixer.NeoMixer' in icon, f'icon: {icon}')
    d.check('Quit NeoMixer' in layout, f'menu: {layout[:160]}')

    # what a panel asks first
    everything = gdbus(item, 'org.freedesktop.DBus.Properties.GetAll', 'org.kde.StatusNotifierItem')
    yield 0.3
    everything = everything.communicate()[0]
    for name in ('Status', 'Category', 'Menu', 'ToolTip', 'ItemIsMenu'):
        d.check(f"'{name}'" in everything, f'{name} is readable')

    gdbus(item, 'org.kde.StatusNotifierItem.Activate', '0', '0')
    yield 0.5
    d.check(d.app.window.get_visible() is False, 'left click hides the window')
    gdbus(item, 'org.kde.StatusNotifierItem.Activate', '0', '0')
    yield 0.5
    d.check(d.app.window.get_visible() is True, 'left click shows it')

    gdbus(menu, 'com.canonical.dbusmenu.Event', '3', 'clicked', '<"">', '0')
    yield 2
    d.check(False, 'menu entry should have quit the app')


def tray_registers_with_the_panel(d):
    '''A panel appears after the app, and the icon has to find it: as when plasmashell restarts.'''
    import os
    import subprocess
    import sys

    watcher = subprocess.Popen(
        [sys.executable, os.path.join(os.path.dirname(__file__), 'fake_watcher.py')], stdout=subprocess.PIPE, text=True
    )
    yield 1.0
    d.check(d.app.tray.registered, 'icon registered itself')
    yield 1.5
    output = watcher.communicate(timeout=5)[0]
    d.check(d.app.tray.bus_name in output, f'panel got its name: {output.strip()}')
    yield 0.3
    d.check(not d.app.tray.registered, 'noticed the panel is gone')


def quit_command(d):
    '''"neomixer quit" stops a running instance.'''
    import subprocess
    import sys
    subprocess.Popen([sys.executable, '-m', 'neomixer.main', 'quit'])
    yield 3
    d.check(False, 'should have quit')


def change_then_sigterm(d):
    '''A session ending sends SIGTERM, which must save at once.'''
    import os
    import signal

    strip(d, 'vi', 'in discord').volume_widget.set_value(33)
    yield 0.05
    os.kill(os.getpid(), signal.SIGTERM)
    yield 3


def saved_by_sigterm(d):
    d.check(strip(d, 'vi', 'in discord').device_model.volume[0] == 33, 'change from just before SIGTERM is there')
    yield 0.1


def device_management(d):
    '''Adding, changing and removing devices through what the popovers emit.'''
    from neomixer.scripts import pmctl
    from tests.integration.ui_driver import links

    outputs = d.content.device_box['a'].widgets
    tags = [widget.tag_label.get_label() for _, widget in sorted(outputs.items(), key=lambda item: int(item[0]))]
    d.check(tags == ['A1', 'A2', 'A3', 'A4'], f'output tags count up despite gaps in the ids: {tags}')

    popover = d.content.create_device_button['vi'].get_popover()
    popover.nick_widget.set_option('new input')
    popover.name_widget.set_option('newinput')
    popover.combobox_widget.set_active_name('Stereo')
    d.content._on_device_create(None, popover)
    yield 0.5
    new = next((w for w in d.content.device_box['vi'].widgets.values() if w.device_model.name == 'newinput'), None)
    d.check(new is not None, 'new strip appears')
    d.check(pmctl.get_device_by_name('sink', 'newinput') is not None, 'with a sink')
    data = [entry[1] for entry in d.card('firefox').combobox._device_data['sink_input']]
    d.check('newinput' in data, 'programs can be routed to it')
    d.check(d.card('firefox').entry.device == 'inmedia', 'programs keep their output')
    d.check(d.card('firefox').combobox.get_active_text() == 'inmedia', 'and still show it')

    new.connections_widgets['a'].widgets['1'].toggle_button.set_active(True)
    yield 0.3
    d.check('newinput:monitor_FL' in links(), 'route works')
    d.shot('new_device')

    # the settings popup of a strip
    new.edit_button.get_popover().popup()
    new._on_edit_pressed()
    yield 0.3
    d.shot('device_settings', new.edit_button.get_popover())
    new.edit_button.get_popover().popdown()

    # EQ on it, then remove it: the EQ has to go too
    from neomixer.scripts import pw_eq
    new.device_model.eq.enabled = True
    d.ui.open_eq_window(None, 'vi', list(d.content.device_box['vi'].widgets)[-1])
    device_id = next(key for key, w in d.content.device_box['vi'].widgets.items() if w is new)
    d.ui._on_eq_window_changed(None, 'vi', device_id)
    yield 0.8
    d.check(bool(pw_eq.running_pids('newinput')), 'EQ runs on the new device')

    # rename: new sink, EQ and routes follow
    schema = new.device_model.model_dump(exclude={'connections', 'eq', 'init_failed', 'init_error', 'present'})
    schema.update(name='renamed', nick='renamed', description='renamed')
    new.emit('device_change', schema)
    yield 1.0
    d.check(pmctl.get_device_by_name('sink', 'newinput') is None, 'old sink is gone')
    d.check(pmctl.get_device_by_name('sink', 'renamed') is not None, 'renamed sink exists')
    linked = links()
    d.check('input.nm_eq_renamed:input_FL' in linked, 'EQ follows')
    d.check(not pw_eq.running_pids('newinput'), 'old EQ is gone')
    d.check('output.nm_eq_renamed:output_FL' in linked, 'route follows')
    d.check(new.nick_label.get_label() == 'renamed', 'strip shows the name')

    new.popover.remove_button.emit('clicked')
    yield 0.8
    d.check(device_id not in d.content.device_box['vi'].widgets, 'strip is gone')
    d.check(pmctl.get_device_by_name('sink', 'renamed') is None, 'sink is gone')
    d.check(not pw_eq.running_pids('renamed'), 'EQ is gone')

    # a new output adds a route to every input
    popover = d.content.create_device_button['b'].get_popover()
    popover.nick_widget.set_option('bus2')
    popover.name_widget.set_option('bus2')
    popover.combobox_widget.set_active_name('Stereo')
    d.content._on_device_create(None, popover)
    yield 0.5
    media = strip(d, 'vi', 'in media')
    d.check(len(media.connections_widgets['b'].widgets) == 2, 'inputs get a route to the new output')
    d.check(strip(d, 'b', 'bus2').tag_label.get_label() == 'B2', 'tagged B2')
    d.check(d.content.mixer_grid.get_parent().get_hadjustment().get_upper() <= d.content.mixer_grid.get_parent().get_hadjustment().get_page_size() + 1, 'still fits')
    d.shot('two_buses')
    strip(d, 'b', 'bus2').popover.remove_button.emit('clicked')
    yield 0.5
    d.check(len(media.connections_widgets['b'].widgets) == 1, 'and lose it again')


def cpu_usage(d):
    '''A mixer stays open all day: it must be light, and next to nothing while hidden.'''
    import os
    import time

    def cpu_seconds():
        return sum(os.times()[:2])

    d.play('Firefox', binary='firefox', freq=300)
    d.play('Discord', binary='Discord', freq=500)
    yield 2.0
    start, started = cpu_seconds(), time.monotonic()
    yield 6.0
    percent = 100 * (cpu_seconds() - start) / (time.monotonic() - started)
    print(f'cpu with the window open, two programs playing: {percent:.1f} %')
    d.check(percent < 25, f'light while visible: {percent:.1f} % of a core')

    d.stop_players()
    d.ui.hide_window()
    yield 1.0
    start, started = cpu_seconds(), time.monotonic()
    yield 6.0
    percent = 100 * (cpu_seconds() - start) / (time.monotonic() - started)
    print(f'cpu hidden: {percent:.1f} %')
    d.check(percent < 2, f'next to nothing while hidden: {percent:.1f} % of a core')
    d.ui.show_window()
    yield 0.2


def close_quits_when_background_is_off(d):
    d.app.config_model.background = False
    yield 0.2
    d.app.window.close()
    yield 2
    d.check(False, 'closing should have quit')


def starts_hidden(d):
    from tests.integration.ui_driver import stream_device
    d.check(d.app.window.get_visible() is False, 'starts without a window')
    d.check(not d.ui.vumeter_tasks['vi'], 'and without meters')
    d.play('Firefox', binary='firefox', freq=330)
    yield 0.8
    d.check(stream_device('Firefox') == 'inmedia', 'routing works')
    d.stop_players()
    d.app.config_model.background = True
    yield 0.1


def live_hold(d):
    '''
    For a real desktop, not part of the suite: what Steam writes, against the real session manager.
    Programs on the default leave it for a moment, and every setting ends as it started.
    '''
    import time

    for device_type, kind, others in (('vi', 'sink', 'a'), ('b', 'source', 'hi')):
        held = next(widget for widget in d.content.device_box[device_type].widgets.values() if widget.device_model.primary)
        other = next(widget.device_model.name for widget in d.content.device_box[others].widgets.values() if widget.device_model.present)
        name = held.device_model.name
        held.hold_widget.set_active(True)
        yield 0.3
        d.check(_default(kind) == name, f'{name} is the default {kind}')

        started = time.monotonic()
        _take_default(kind, other)
        changed = False
        while time.monotonic() - started < 3:
            current = _default(kind)
            changed = changed or current != name
            if changed and current == name:
                break
            yield 0.002
        print(f'{kind}: {other} took it, back on {name} after {(time.monotonic() - started) * 1000:.0f} ms')
        yield 0.5
        d.check(_default(kind) == name, f'stays on {name}: {_default(kind)}')
        d.check(held.primary_widget.get_active(), 'the star stayed')

        held.hold_widget.set_active(False)
        yield 0.2
        d.check(_default(kind) == name, f'lets go without changing it: {_default(kind)}')
    yield 2.5  # the autosave


def live_output(d):
    '''
    For a real desktop, not part of the suite: a real sound card, with a config of one virtual
    input routed to it, and short test tones that can be heard. Removes what it made when it ends.
    '''
    import subprocess
    from neomixer.scripts import pw_eq
    from tests.integration.ui_driver import links

    name = next(iter(d.content.device_box['a'].widgets.values())).device_model.name
    virtual = next(iter(d.content.device_box['vi'].widgets.values()))
    original = subprocess.check_output(['pactl', 'get-sink-volume', name], text=True).split('/')[1].strip()
    subprocess.run(['pactl', 'set-sink-volume', name, '25%'], check=True)
    yield 0.5
    d.check(_default('sink') == virtual.device_model.name, f'the virtual input is the default: {_default("sink")}')

    before = _measure_at_device(name, ['--target', virtual.device_model.name])
    print(f'route without EQ: {before}')
    d.check(before is not None, 'audio reaches the sound card through the route')
    window = _set_band(d, 'a', '1', 9)
    yield 1.5
    d.check(bool(pw_eq.running_pids(name)), 'EQ runs')
    d.check(f'output.nm_eq_{name}:output_FL\n  |-> {name}:playback_FL' in links(), 'the session manager links it to the sound card')

    routed = _measure_at_device(name, ['--target', virtual.device_model.name])
    print(f'route with EQ: {routed}')
    d.check(routed is not None and abs(routed - before - 9) < 0.7, f'routed input gets +9 dB: {routed - before if routed else None}')
    direct = _measure_at_device(name, ['--target', name])
    print(f'program on the output with EQ: {direct}')
    d.check(direct is not None and abs(direct - before - 9) < 1.5, f'program on the output gets +9 dB: {direct - before if direct else None}')

    window.enable_switch.set_active(False)
    yield 0.4
    off = _measure_at_device(name, ['--target', virtual.device_model.name])
    print(f'EQ off: {off}')
    d.check(off is not None and abs(off - before) < 0.5, f'off passes through: {off - before if off else None}')
    window.enable_switch.set_active(True)
    yield 0.4

    # What Steam does, with the real sound card and its EQ there
    virtual.hold_widget.set_active(True)
    yield 0.3
    _take_default('sink', name)
    yield 0.5
    d.check(_default('sink') == virtual.device_model.name, f'the default comes back: {_default("sink")}')
    virtual.hold_widget.set_active(False)
    yield 0.2
    d.shot('live_output')

    window.eq.reset()
    window.eq.enabled = False
    window._edited()
    yield 0.3
    subprocess.run(['pactl', 'set-sink-volume', name, original], check=True)
    d.app.config_model.cleanup = True


def live_check(d):
    '''
    For a real desktop, not part of the suite: real monitors, the real theme and a real output.
    The EQ stays flat, nothing audible changes, and every setting ends as it started.
    '''
    import json
    import subprocess
    from gi.repository import Gdk, Gtk
    from neomixer.clients.gtk import text_size
    from neomixer.scripts import pmctl, pw_eq
    from tests.integration.ui_driver import links

    window = d.app.window
    scaler = d.ui.text_scaler
    points = d.app.config_model.text_size
    monitors = Gdk.Display.get_default().get_monitors()
    for index in range(monitors.get_n_items()):
        monitor = monitors.get_item(index)
        dpi = text_size.monitor_dpi(monitor)
        print(f'monitor {monitor.get_connector()} {monitor.get_model()}: {dpi:.1f} dpi, {points} pt is '
              f'{text_size.text_px(points, dpi):.2f} px, {text_size.text_px(points, dpi) / dpi * 25.4:.2f} mm')

    yield 1.0
    here = Gdk.Display.get_default().get_monitor_at_surface(window.get_surface())
    expected = round(text_size.text_px(points, text_size.monitor_dpi(here)), 2)
    d.check(scaler.px_of(window) == expected, f'window on {here.get_connector()} has {scaler.px_of(window)} px')
    nick = next(iter(d.content.device_box['vi'].widgets.values())).nick_label
    d.check(abs(_font_px(nick) - expected) < 0.2, f'drawn so with the theme {Gtk.Settings.get_default().get_property("gtk-theme-name")}: {_font_px(nick)}')
    d.shot('live_main')

    # a setting reaches all text in the real theme
    box = d.content.settings_box
    d.ui.settings_menu_open(None)
    popover = d.content.settings_button.get_popover()
    popover.popup()
    box.text_size.set_value(points + 4)
    yield 0.4
    big = scaler.px_of(window)
    too_small = [label.get_label() for label in _labels(window) if label.get_mapped() and _font_px(label) < big * 0.6]
    d.check(not too_small, f'all text follows the setting: {too_small[:5]}')
    settings_label = box.get_first_child().get_first_child()
    d.check(abs(_font_px(settings_label) - big) < 0.2, f'popover too: {_font_px(settings_label)} of {big}')
    d.shot('live_settings', popover)
    box.text_size.set_value(points)
    popover.popdown()
    yield 0.3

    # a real move to the other monitor
    other_index = next(i for i in range(monitors.get_n_items()) if monitors.get_item(i).get_connector() != here.get_connector())
    other = monitors.get_item(other_index)
    script = (
        'var w = workspace.windowList().filter(function (c) { return c.caption == "NeoMixer"; })[0];'
        'var o = workspace.screens.filter(function (s) { return s.name == "%s"; })[0];'
        'workspace.sendClientToScreen(w, o);'
    )

    def move_to(connector):
        import os
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as out:
            out.write(script % connector)
        bus = ['gdbus', 'call', '--session', '--dest', 'org.kde.KWin']
        loaded = subprocess.run([*bus, '--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.loadScript', out.name, 'neomixer-check'],
                                capture_output=True, text=True, check=False).stdout
        script_id = loaded.strip().strip('(),').split()[-1]
        subprocess.run([*bus, '--object-path', f'/Scripting/Script{script_id}', '--method', 'org.kde.kwin.Script.run'], capture_output=True, check=False)
        subprocess.run([*bus, '--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.unloadScript', 'neomixer-check'], capture_output=True, check=False)
        os.unlink(out.name)

    start_connector = here.get_connector()
    move_to(other.get_connector())
    yield 1.2
    now = Gdk.Display.get_default().get_monitor_at_surface(window.get_surface())
    d.check(now.get_connector() == other.get_connector(), f'moved to {now.get_connector()}')
    wanted = round(text_size.text_px(points, text_size.monitor_dpi(other)), 2)
    d.check(scaler.px_of(window) == wanted, f'text follows: {scaler.px_of(window)} px, wanted {wanted}')
    d.check(abs(_font_px(nick) - wanted) < 0.2, f'drawn so: {_font_px(nick)}')
    d.shot('live_other_monitor')
    move_to(start_connector)
    yield 1.0

    # a flat EQ on the output that is playing
    sinks = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks'], text=True))
    state = {sink['name']: sink['state'] for sink in sinks}
    outputs = d.app.device_repository.get_devices_by_type('a')
    device_id, device = next(((key, dev) for key, dev in outputs.items() if state.get(dev.name) == 'RUNNING'),
                             next(((key, dev) for key, dev in outputs.items() if dev.present), (None, None)))
    print('output under test:', device.nick, device.name, state.get(device.name))
    was_enabled = device.eq.enabled
    if was_enabled:
        print('it already has an EQ on, not touching it')
        return

    device.eq.enabled = True
    d.ui._on_eq_window_changed(None, 'a', device_id)
    yield 1.5
    capture, playback = pw_eq.eq_nodes(device.name)
    d.check(bool(pw_eq.running_pids(device.name)), 'EQ process runs')
    d.check(any(target.startswith(f'{device.name}:') for _source, target in pmctl.get_links(playback)), 'WirePlumber linked it to the real device')
    d.check(pmctl.get_device_by_name('sink', capture) is not None, 'it exists as a filter sink')
    default = subprocess.check_output(['pactl', 'get-default-sink'], text=True).strip()
    d.check(not default.startswith('input.nm_eq_'), f'default device untouched: {default}')
    d.check(not [dev.name for dev in d.ui.list_devices('a') if 'nm_eq_' in dev.name], 'not offered as a device')
    sinks = json.loads(subprocess.check_output(['pactl', '-f', 'json', 'list', 'sinks'], text=True))
    filter_sink = next(sink for sink in sinks if sink['name'] == capture)
    print('filter sink flags:', filter_sink.get('flags'), '| description:', filter_sink.get('description'), '| state', filter_sink['state'])
    linked = links()
    print('into the filter:', linked.count(f'-> {capture}:'))
    for app_type, entries in d.app.app_registry.entries.items():
        for entry in entries.values():
            for stream in entry.streams.values():
                print(f'  stream {entry.label}: {stream.device}')
    print(subprocess.check_output(['pw-top', '-b', '-n', '2'], text=True)[-900:])

    device.eq.enabled = False
    d.ui._on_eq_window_changed(None, 'a', device_id)
    yield 0.3


def output_eq_and_text_then_killed(d):
    '''Set an EQ on both kinds of output and a text size, and die without a chance to save.'''
    import os
    import signal

    _set_band(d, 'a', '1', 9)
    _set_band(d, 'b', '1', 6)
    d.ui.settings_menu_open(None)
    d.content.settings_box.text_size.set_value(11.5)
    yield 3.5  # past the autosave
    os.kill(os.getpid(), signal.SIGKILL)
    yield 1


def output_eq_and_text_after_reboot(d):
    from neomixer.scripts import pw_eq
    from tests.integration.ui_driver import links

    headset, bus = strip(d, 'a', 'headset'), strip(d, 'b', 'mic1')
    d.check(d.app.config_model.text_size == 11.5, f'text size survived: {d.app.config_model.text_size}')
    d.check(d.content.settings_box.text_size.get_value() == 11.5, 'settings show it')
    d.check(headset.device_model.eq.enabled and headset.device_model.eq.bands[3].gain == 9, 'hardware output EQ settings survived')
    d.check(bus.device_model.eq.enabled and bus.device_model.eq.bands[3].gain == 6, 'virtual output EQ settings survived')
    d.check('eq-on' in headset.eq_button.get_css_classes(), 'buttons show it')
    d.check(bool(pw_eq.running_pids(HEADSET)) and bool(pw_eq.running_pids('mic1')), 'both are running')

    linked = links()
    d.check(f'output.nm_eq_{HEADSET}:output_FL\n  |-> {HEADSET}:playback_FL' in linked, 'hardware EQ is linked to its device')
    d.check('output.nm_eq_mic1:output_MONO\n  |-> mic1:input_MONO' in linked, 'virtual EQ too')

    strip(d, 'vi', 'in discord').volume_widget.set_value(100)
    yield 0.2
    routed = _measure_at_device(HEADSET, ['--target', 'indiscord'])
    d.check(routed is not None and abs(routed - 9) < 0.7, f'and it sounds like it: {routed}')
    direct = _measure_at_device(HEADSET, ['--target', HEADSET])
    d.check(direct is not None and direct > routed - 3 and direct > -60, f'programs go through it too: {direct}')

    # back to neutral for what runs next
    for key in (('a', '1'), ('b', '1')):
        d.ui.open_eq_window(None, *key)
        window = d.ui.eq_windows[key]
        window.eq.reset()
        window.eq.enabled = False
        window._edited()
    d.content.settings_box.text_size.set_value(9)
    yield 0.4


def eq_starts_when_its_output_arrives(d):
    '''After a reboot sound cards can show up after the app: the EQ has to wait for them.'''
    import subprocess
    from neomixer.scripts import pw_eq
    from tests.integration.ui_driver import create_null_sink, links

    blue = 'bluez_output.80_99_E7_ED_23_2C.1'
    subprocess.run(['pw-cli', 'destroy', blue], check=False, stdout=subprocess.DEVNULL)
    yield 0.6
    strip_widget = strip(d, 'a', 'blue')
    strip_widget.device_model.eq.enabled = True
    strip_widget.device_model.eq.bands[3].freq = 1000
    strip_widget.device_model.eq.bands[3].gain = 9
    d.ui._on_eq_window_changed(None, 'a', '4')
    yield 0.5
    d.check(not pw_eq.running_pids(blue), 'no EQ without its device')

    strip(d, 'vi', 'in discord').volume_widget.set_value(100)
    create_null_sink(blue)
    yield 1.5
    d.check(bool(pw_eq.running_pids(blue)), 'it starts when the device arrives')
    d.check(f'output.nm_eq_{blue}:output_FL\n  |-> {blue}:playback_FL' in links(), 'and gets linked to it')
    routed = _measure_at_device(blue, ['--target', 'indiscord'])
    d.check(routed is not None and abs(routed - 9) < 0.7, f'routes go through it: {routed}')

    strip_widget.device_model.eq.reset()
    strip_widget.device_model.eq.enabled = False
    d.ui._on_eq_window_changed(None, 'a', '4')
    yield 0.3
