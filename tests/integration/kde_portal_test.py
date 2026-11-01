'''
Hotkeys with nothing faked: KWin, its global shortcuts, xdg-desktop-portal and KDE's backend for it,
real key presses and the real dialog. These are the desktop's own services on a bus that can start
them, so it only runs in a container: see packaging/test-in-container.sh.

    python tests/integration/kde_portal_test.py [screenshot dir]
'''
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tests.integration import lifecycle_test  # noqa: E402



def start_session(root):
    runtime = os.path.join(root, 'run')
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    env = dict(
        os.environ, HOME=root, XDG_RUNTIME_DIR=runtime, XDG_CONFIG_HOME=os.path.join(root, 'config'),
        XDG_STATE_HOME=os.path.join(root, 'state'), XDG_DATA_HOME=os.path.join(root, 'data'),
        XDG_CURRENT_DESKTOP='KDE', XDG_SESSION_TYPE='wayland', KDE_FULL_SESSION='true', KDE_SESSION_VERSION='6',
        WAYLAND_DISPLAY='wl-kde', QT_QPA_PLATFORM='wayland', GDK_BACKEND='wayland', GSK_RENDERER='cairo', GTK_A11Y='none',
        KWIN_WAYLAND_NO_PERMISSION_CHECKS='1', KWIN_SCREENSHOT_NO_PERMISSION_CHECKS='1',
        NEOMIXER_KDE_SESSION=root, PYTHONPATH=f'{lifecycle_test.CODE}:{ROOT}',
    )
    bus = subprocess.run(['dbus-daemon', '--session', '--fork', '--print-address=1', '--print-pid=1', '--nopidfile'], env=env,
                         capture_output=True, text=True, check=True)
    address, bus_pid = bus.stdout.split()
    env['DBUS_SESSION_BUS_ADDRESS'] = address

    processes = []

    def start(*command, wait=0.5):
        with open(os.path.join(root, f'{command[0]}.log'), 'w', encoding='utf-8') as log:
            processes.append(subprocess.Popen(command, env=env, stdout=log, stderr=log))
        time.sleep(wait)

    start('kwin_wayland', '--virtual', '--no-lockscreen', '--no-kactivities', '--socket', 'wl-kde',
          '--width', '1600', '--height', '1000', wait=4)
    start('pipewire')
    start('wireplumber')
    start('pipewire-pulse', wait=1)

    def stop():
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)
        os.kill(int(bus_pid), signal.SIGTERM)  # the portal and what else it started go with their bus
        time.sleep(2)

    return env, stop


def main():
    if not os.path.exists('/run/.containerenv'):
        sys.exit("Only in a container: on a desktop this would start a portal next to the desktop's own")

    shots = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else tempfile.mkdtemp(prefix='kde-shots-')
    root = tempfile.mkdtemp(prefix='kde-session-')
    env, stop = start_session(root)
    results = []
    try:
        config = os.path.join(env['XDG_CONFIG_HOME'], 'neomixer', 'config.json')
        os.makedirs(os.path.dirname(config))
        with open(config, 'w', encoding='utf-8') as out:
            json.dump(lifecycle_test.FIXTURE, out)
        lifecycle_test.fake_hardware(env)
        results.append(('kde_hotkeys_set', lifecycle_test.run_scenario('kde_hotkeys_set', 0, env, shots)))
        stop()

        # The whole session again: what is left is the settings on disk, as after a reboot
        print('=== rebooted', flush=True)
        env, stop = start_session(root)
        lifecycle_test.fake_hardware(env)
        results.append(('kde_hotkeys_after_reboot', lifecycle_test.run_scenario('kde_hotkeys_after_reboot', 0, env, shots)))
    finally:
        stop()

    failed = [name for name, passed in results if not passed]
    print(f'{len(results) - len(failed)} of {len(results)} scenarios with real KDE passed')
    for name in failed:
        print('  FAILED', name)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
