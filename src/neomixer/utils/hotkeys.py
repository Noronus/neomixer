'''
What a hotkey is, apart from how the desktop delivers it: what it does, and how its keys are written.
Keys are written as in the XDG shortcuts specification, the form portals take them in: CTRL+ALT+m
'''
import re

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

ACTIONS = ('mute', 'up', 'down')

# In the order they are written
MODIFIER_LABELS = {'LOGO': 'Meta', 'CTRL': 'Ctrl', 'ALT': 'Alt', 'SHIFT': 'Shift'}

# Keys that no program needs, taking them alone costs nothing
SPARE_KEYS = re.compile(r'^(XF86\w+|F(1[3-9]|[2-9]\d)|Pause|Scroll_Lock)$')

# The range of keyvals that GDK leaves the XF86 out of
XF86_KEYS = (0x10080000, 0x1008ffff)

KEY_LABELS = {'Page_Up': 'PgUp', 'Page_Down': 'PgDown', 'Prior': 'PgUp', 'Next': 'PgDown', 'Return': 'Enter', 'space': 'Space'}


def shortcut_id(device_type, device_id, action, trigger):
    '''
    The key is part of the id: a desktop never takes a new key for a shortcut it knows, it only
    asks about new ones. Another key is so another shortcut, and the old one goes.
    '''
    return f'{device_type}-{device_id}-{action}-{trigger}'


def parse_shortcut_id(shortcut):
    '''Returns (device_type, device_id, action, trigger), or None if it is not one of ours.'''
    parts = shortcut.split('-', 3)
    if len(parts) != 4 or parts[2] not in ACTIONS:
        return None
    return tuple(parts)


def key_name(keyval):
    '''
    The name of a key for the desktop. GDK leaves out the XF86 of multimedia keys, that is in the
    name of the key everywhere else.
    '''
    name = Gdk.keyval_name(keyval)
    return f'XF86{name}' if XF86_KEYS[0] <= keyval <= XF86_KEYS[1] and not name.startswith('XF86') else name


def make_trigger(modifiers, key):
    '''
        "modifiers" are names from MODIFIER_LABELS
        "key" is the name of a key, see key_name
    '''
    return '+'.join([name for name in MODIFIER_LABELS if name in modifiers] + [key])


def key_label(key):
    '''What is printed on a key, as far as its name tells.'''
    if key in KEY_LABELS:
        return KEY_LABELS[key]
    character = chr(Gdk.keyval_to_unicode(Gdk.keyval_from_name(key)))
    if character.isprintable() and character != '\0' and not key.startswith('KP_'):
        return character.upper()
    return key.removeprefix('XF86')


def trigger_label(trigger):
    '''How a trigger is shown, until the desktop says how it shows it: Ctrl+Alt+M'''
    *modifiers, key = trigger.split('+')
    return '+'.join([MODIFIER_LABELS.get(name, name) for name in modifiers] + [key_label(key)])


def works_alone(key):
    '''Can this key be a hotkey without a modifier? Not one that types: nothing could be typed with it any more.'''
    return key.startswith('KP_') or Gdk.keyval_to_unicode(Gdk.keyval_from_name(key)) == 0


def is_spare(key):
    '''Does a hotkey on this key alone take nothing away from other programs?'''
    return SPARE_KEYS.match(key) is not None
