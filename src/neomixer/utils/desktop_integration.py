'''
Puts the launcher and icon where the desktop looks for them. pipx and pip --user keep a
package's data files inside the package, where no menu ever finds them.
'''
import os
import logging

from neomixer.settings import APP_ID, DATA_DIR, XDG_DATA_HOME
from neomixer.utils.autostart import launch_command

LOG = logging.getLogger('generic')


def _write_if_changed(path, content):
    '''A desktop rebuilds its menu whenever one of these files is touched, so it only happens if needed.'''
    try:
        with open(path, encoding='utf-8') as current:
            if current.read() == content:
                return
    except OSError:
        pass

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as out:
        out.write(content)


def has_system_launcher():
    '''
    A distro package brings the launcher itself, and takes it along when it is removed. A copy
    in the home directory would outlive the package.
    '''
    data_dirs = os.getenv('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    return any(os.path.exists(os.path.join(data_dir, 'applications', f'{APP_ID}.desktop')) for data_dir in data_dirs.split(':'))


def install_launcher():
    '''Install or update the launcher and icon for the current user, if nothing else provides them.'''
    if has_system_launcher():
        return

    try:
        with open(os.path.join(DATA_DIR, f'{APP_ID}.desktop'), encoding='utf-8') as template:
            desktop_entry = template.read().replace('@EXEC@', launch_command())
        _write_if_changed(os.path.join(XDG_DATA_HOME, 'applications', f'{APP_ID}.desktop'), desktop_entry)

        icon_path = os.path.join('icons', 'hicolor', 'scalable', 'apps', f'{APP_ID}.svg')
        with open(os.path.join(DATA_DIR, icon_path), encoding='utf-8') as icon:
            _write_if_changed(os.path.join(XDG_DATA_HOME, icon_path), icon.read())
    except OSError as e:
        LOG.warning('Could not install the launcher: %s', e)
