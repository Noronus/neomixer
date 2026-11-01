'''
The whole app against a real, private sound server: a clean sandbox, then scenarios one after
the other, as separate runs of the app.

    python tests/integration/lifecycle_test.py [screenshot dir] [only these scenarios...]
'''
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# One per run: a second session in the same checkout must not stop this one's sandbox
SANDBOX = os.path.join(ROOT, f'.sandbox-test-{os.getpid()}')
SCRIPT = os.path.join(ROOT, 'tests', 'integration', 'sandbox.sh')
DRIVER = os.path.join(ROOT, 'tests', 'integration', 'ui_driver.py')
PYTHON = sys.executable
# The code under test: this checkout, or an installed package
CODE = os.environ.get('NEOMIXER_CODE', os.path.join(ROOT, 'src'))

HEADSET = 'alsa_output.test_headset.analog-stereo'
SPEAKERS = 'alsa_output.test_card.analog-stereo'
BLUETOOTH = 'bluez_output.80_99_E7_ED_23_2C.1'
FRAME = 'vrlink-sink'
MIC = 'alsa_input.test_headset.mono-fallback'


def _device(name, nick, device_type, device_class, channels=2, description=None, routes=None):
    channel_list = ['MONO'] if channels == 1 else ['FL', 'FR']
    device = {
        'name': name, 'nick': nick, 'description': description or nick, 'device_type': device_type,
        'device_class': device_class, 'volume': [100] * channels, 'mute': False, 'flags': 0,
        'external': False, 'primary': False if device_class == 'virtual' else None, 'channels': channels,
        'channel_list': channel_list, 'connections': {'a': {}, 'b': {}}, 'selected_channels': [True] * channels,
    }
    for output_type, output_id, nick_, output_channels in OUTPUTS:
        device['connections'][output_type][output_id] = {
            'nick': nick_, 'state': (output_type, output_id) in (routes or ()), 'latency': None, 'auto_ports': True,
            'input_sel_channels': [True] * channels, 'output_sel_channels': [True] * output_channels,
            'port_map': [], 'route_volume': 100, 'use_loopback': False,
        }
    return device


OUTPUTS = (('a', '1', 'headset', 2), ('a', '2', 'speakers', 2), ('a', '4', 'blue', 2), ('a', '5', 'frame', 2), ('b', '1', 'mic1', 1))
# The shape a Pulsemeeter 2.1 config has, ids with gaps included
FIXTURE = {
    'devices': {
        'vi': {
            '1': _device('in1', 'in1', 'sink', 'virtual', routes=[('a', '1'), ('a', '4')]),
            '2': _device('inmedia', 'in media', 'sink', 'virtual', routes=[('a', '1'), ('a', '2')]),
            '3': _device('indiscord', 'in discord', 'sink', 'virtual', routes=[('a', '1'), ('a', '2'), ('a', '4')]),
        },
        'hi': {'1': _device(MIC, 'mic', 'source', 'hardware', channels=1, description='Headset Mono', routes=[('b', '1')])},
        'a': {
            '1': _device(HEADSET, 'headset', 'sink', 'hardware', description='Test Headset'),
            '2': _device(SPEAKERS, 'speakers', 'sink', 'hardware', description='Test Speakers'),
            '4': _device(BLUETOOTH, 'blue', 'sink', 'hardware', description='WH-1000XM6'),
            '5': _device(FRAME, 'frame', 'sink', 'hardware', description='Steam Frame'),
        },
        'b': {'1': _device('mic1', 'mic1', 'source', 'virtual', channels=1)},
    },
    'vumeters': True, 'cleanup': False, 'tray': False, 'layout': 'Bars', 'window_width': 1887, 'window_height': 816,
}

# name, exit code to expect
SCENARIOS = (
    ('assign_and_restart_program', 0),
    ('setup_while_offline', 0),
    ('idle_client_is_listed', 0),
    ('settings_apply', 0),
    ('crowded', 0),
    ('device_management', 0),
    ('cpu_usage', 0),
    ('set_everything_then_get_killed', -signal.SIGKILL),
    ('REBOOT', None),
    ('all_restored_after_reboot', 0),
    ('eq_changes_the_sound', 0),
    ('hotplug', 0),
    ('change_then_sigterm', 0),
    ('saved_by_sigterm', 0),
    ('server_restart', 0),
    ('close_keeps_running', 0),
    ('default_is_held', 0),
    ('hold_then_killed_and_default_taken', -signal.SIGKILL),
    ('hold_wins_at_start', 0),
    ('hold_then_killed', -signal.SIGKILL),
    ('REBOOT', None),
    ('hold_after_reboot', 0),
    ('text_size_setting', 0),
    ('text_follows_the_monitor', 0),
    ('output_eq', 0),
    ('output_eq_and_text_then_killed', -signal.SIGKILL),
    ('REBOOT', None),
    ('output_eq_and_text_after_reboot', 0),
    ('eq_starts_when_its_output_arrives', 0),
    ('close_quits_when_background_is_off', 0),
    ('starts_hidden', 0),
    ('tray_registers_with_the_panel', 0),
    ('tray_is_on_the_bus', 0),
    ('quit_command', 0),
    ('demo_screenshot', 0),
)


def document_portal_mount():
    '''
    The desktop's Flatpak document portal mount, with its mount id. A sandbox that starts a
    portal of its own takes this over, and no Flatpak app starts anymore once that one exits.
    '''
    target = os.path.join(os.environ.get('XDG_RUNTIME_DIR', ''), 'doc')
    with open('/proc/self/mountinfo', encoding='utf-8') as mounts:
        return next((line.split()[0] for line in mounts if line.split()[4] == target), None)


def processes_of_the_sandbox(env):
    '''Whatever still runs with the sandbox's bus or directories in its environment.'''
    markers = (env['DBUS_SESSION_BUS_ADDRESS'].encode(), SANDBOX.encode())
    found = []
    for pid in filter(str.isdigit, os.listdir('/proc')):
        try:
            with open(f'/proc/{pid}/environ', 'rb') as environ:
                if any(marker in environ.read() for marker in markers):
                    with open(f'/proc/{pid}/comm', encoding='utf-8') as comm:
                        found.append(f'{comm.read().strip()} ({pid})')
        except OSError:
            continue
    return found


def sandbox_env():
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join((CODE, ROOT))
    with open(os.path.join(SANDBOX, 'env'), encoding='utf-8') as env_file:
        for line in env_file:
            parts = shlex.split(line)
            if parts and parts[0] == 'export':
                key, _, value = parts[1].partition('=')
                env[key] = value
            elif parts and parts[0] == 'unset':
                for key in parts[1:]:
                    env.pop(key, None)
    return env


def fake_hardware(env):
    code = (
        'from tests.integration.ui_driver import fake_hardware_of; import json, sys; '
        'fake_hardware_of(json.loads(sys.argv[1]))'
    )
    subprocess.run([PYTHON, '-c', code, json.dumps(FIXTURE)], env=env, check=True, capture_output=True)


def run_scenario(name, expected, env, shots):
    print(f'=== {name}', flush=True)
    started = time.monotonic()
    run = subprocess.run([PYTHON, DRIVER, name, shots], env=env, capture_output=True, text=True, timeout=240, check=False)
    lines = [line for line in run.stdout.splitlines() if line.startswith(('ok ', 'FAIL', 'screenshot', 'cpu', 'taken back', 'times taken'))
             or ' times taken back' in line]
    errors = [line for line in run.stderr.splitlines() if 'Traceback' in line or '[ERROR]' in line or 'Error:' in line
              or 'CRITICAL' in line or ('WARNING' in line and 'Gizmo' not in line)]
    print('\n'.join(lines))
    if errors:
        print('\n'.join(errors))
        print(run.stderr[-3000:])
    # An exception in a callback does not end GTK's main loop, so it does not show in the exit code
    passed = run.returncode == expected and not any(line.startswith('FAIL') for line in lines) and 'Traceback' not in run.stderr
    print(f'--> {"pass" if passed else f"FAILED (exit {run.returncode})"} in {time.monotonic() - started:.1f}s', flush=True)
    return passed


def main():
    shots = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, '.sandbox-shots')
    only = sys.argv[2:]
    portal_before = document_portal_mount()
    subprocess.run([SCRIPT, 'start', SANDBOX], check=True)
    env = sandbox_env()
    results = []
    try:
        legacy = os.path.join(env['XDG_CONFIG_HOME'], 'pulsemeeter', 'config.json')
        os.makedirs(os.path.dirname(legacy))
        with open(legacy, 'w', encoding='utf-8') as out:
            json.dump(FIXTURE, out)
        fake_hardware(env)

        # Someone new has no config at all, and nothing to import
        fresh = dict(env, XDG_CONFIG_HOME=os.path.join(env['XDG_CONFIG_HOME'], 'first-run'))
        if not only or 'first_run' in only:
            results.append(('first_run', run_scenario('first_run', 0, fresh, shots)))

        for name, expected in SCENARIOS:
            if only and name not in only:
                continue

            if name == 'REBOOT':
                subprocess.run([SCRIPT, 'reboot', SANDBOX], check=True)
                fake_hardware(env)
                print('=== rebooted')
                continue

            results.append((name, run_scenario(name, expected, env, shots)))
    finally:
        subprocess.run([SCRIPT, 'stop', SANDBOX], check=False, capture_output=True)

    # The sandbox has to be gone without a trace, the desktop next to it untouched
    time.sleep(1)
    leftovers = processes_of_the_sandbox(env)
    print(f'=== leftovers of the sandbox: {leftovers or "none"}')
    results.append(('no process outlives the sandbox', not leftovers))
    portal_after = document_portal_mount()
    print(f'=== document portal mount: {portal_before} before, {portal_after} after')
    results.append(("the desktop's document portal is untouched", portal_before == portal_after))
    if not leftovers:
        shutil.rmtree(SANDBOX, ignore_errors=True)

    print()
    failed = [name for name, passed in results if not passed]
    print(f'{len(results) - len(failed)} of {len(results)} scenarios passed')
    for name in failed:
        print('  FAILED', name)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
