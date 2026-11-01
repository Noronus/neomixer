'''
Settings for the runtime of neomixer
'''
import gettext
import os
import sys


VERSION = '1.1.0'
APP_NAME = "neomixer"

# config settings
HOME = os.getenv('HOME', os.getenv('USERPROFILE'))
XDG_CONFIG_HOME = os.getenv('XDG_CONFIG_HOME', os.path.join(HOME, '.config'))
XDG_DATA_HOME = os.getenv('XDG_DATA_HOME', os.path.join(HOME, '.local/share'))
XDG_STATE_HOME = os.getenv('XDG_STATE_HOME', os.path.join(HOME, '.local/state'))
APP_RUNTIME_DIR = os.path.dirname(__file__)
XDG_RUNTIME_DIR = os.getenv('XDG_RUNTIME_DIR', os.path.join('/run/user', str(os.getuid())))
CONFIG_DIR = os.path.join(XDG_CONFIG_HOME, 'neomixer')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
LEGACY_CONFIG_FILE = os.path.join(XDG_CONFIG_HOME, 'pulsemeeter', 'config.json')
AUTOSTART_DIR = os.path.join(XDG_CONFIG_HOME, 'autostart')
RUNTIME_DIR = os.path.join(XDG_RUNTIME_DIR, 'neomixer')
DATA_DIR = os.path.join(APP_RUNTIME_DIR, 'data')
APP_ID = 'org.neomixer.NeoMixer'
LOG_DIR = os.path.join(XDG_STATE_HOME, 'neomixer')
LOG_FILE = os.path.join(LOG_DIR, 'neomixer.log')


# logging
DEBUG = False

# Translations
LOCALE_DIR = ''
LOCALE_DIRS = [
    '/usr/share/locale',
    '/usr/local/share/locale',
    os.path.join(APP_RUNTIME_DIR, "locale"),
    os.path.join(sys.prefix, 'share', 'locale'),
    os.path.join(XDG_DATA_HOME, "locale")
]

# Search for translations
for locale_dir in LOCALE_DIRS:
    try:
        gettext.translation(APP_NAME, locale_dir)  # look for .mo files in path
        LOCALE_DIR = locale_dir  # set it when no exception is raised

    # ignore when we dont find it
    except FileNotFoundError:
        pass

gettext.bindtextdomain(APP_NAME, LOCALE_DIR)
gettext.textdomain(APP_NAME)
