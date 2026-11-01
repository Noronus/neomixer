'''
Starting at login through the freedesktop autostart spec, which every desktop understands.
'''
import os
import sys
import shlex
import shutil
import logging

from neomixer.settings import APP_ID, AUTOSTART_DIR

LOG = logging.getLogger('generic')

HIDDEN_FLAG = '--hidden'


def autostart_file() -> str:
    return os.path.join(AUTOSTART_DIR, f'{APP_ID}.desktop')


def launch_command() -> str:
    '''
    The command that starts this very installation: pipx and the like put it where the login
    environment's PATH may not reach.
    '''
    argv0 = sys.argv[0] if sys.argv else ''
    if os.path.basename(argv0) == 'neomixer' and os.path.exists(argv0):
        return shlex.quote(os.path.abspath(argv0))

    found = shutil.which('neomixer')
    if found:
        return shlex.quote(found)

    return f'{shlex.quote(sys.executable)} -m neomixer.main'


def is_enabled() -> bool:
    return os.path.exists(autostart_file())


def starts_hidden() -> bool:
    '''True if the autostart entry starts without a window, or there is no entry yet.'''
    try:
        with open(autostart_file(), encoding='utf-8') as entry:
            return HIDDEN_FLAG in entry.read()
    except OSError:
        return True


def set_enabled(enabled: bool, hidden: bool = True):
    '''Write or remove the autostart entry.'''
    path = autostart_file()
    if not enabled:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return

    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    command = launch_command()
    if hidden:
        command += f' {HIDDEN_FLAG}'

    with open(path, 'w', encoding='utf-8') as entry:
        entry.write(
            '[Desktop Entry]\n'
            'Type=Application\n'
            'Name=NeoMixer\n'
            'Comment=Virtual audio mixer for PipeWire\n'
            f'Exec={command}\n'
            f'Icon={APP_ID}\n'
            'Terminal=false\n'
            'X-GNOME-Autostart-enabled=true\n'
        )
    LOG.debug('Autostart entry written: %s', path)
