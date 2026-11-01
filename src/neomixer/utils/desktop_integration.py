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


def install_launcher():
    '''Install or update the launcher and icon for the current user.'''
    try:
        with open(os.path.join(DATA_DIR, f'{APP_ID}.desktop'), encoding='utf-8') as template:
            desktop_entry = template.read().replace('@EXEC@', launch_command())
        _write_if_changed(os.path.join(XDG_DATA_HOME, 'applications', f'{APP_ID}.desktop'), desktop_entry)

        icon_path = os.path.join('icons', 'hicolor', 'scalable', 'apps', f'{APP_ID}.svg')
        with open(os.path.join(DATA_DIR, icon_path), encoding='utf-8') as icon:
            _write_if_changed(os.path.join(XDG_DATA_HOME, icon_path), icon.read())
    except OSError as e:
        LOG.warning('Could not install the launcher: %s', e)
