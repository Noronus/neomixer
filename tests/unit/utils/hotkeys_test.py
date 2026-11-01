import pytest

from neomixer.clients.gtk.widgets.hotkey_window import is_symbol, modifier_names
from neomixer.utils.hotkeys import make_trigger, parse_shortcut_id, shortcut_id, trigger_label, works_alone

from gi.repository import Gdk


def test_a_shortcut_id_gives_back_what_went_in():
    shortcut = shortcut_id('vi', '12', 'mute', 'CTRL+ALT+m')
    assert parse_shortcut_id(shortcut) == ('vi', '12', 'mute', 'CTRL+ALT+m')


def test_another_key_is_another_shortcut():
    assert shortcut_id('vi', '1', 'up', 'CTRL+a') != shortcut_id('vi', '1', 'up', 'CTRL+b')


@pytest.mark.parametrize('shortcut', ['', 'vi-1-mute', 'vi-1-explode-CTRL+a', 'something else'])
def test_not_our_shortcuts_are_recognized(shortcut):
    assert parse_shortcut_id(shortcut) is None


def test_modifiers_are_written_in_one_order():
    assert make_trigger(['SHIFT', 'ALT', 'CTRL'], 'm') == make_trigger(['CTRL', 'SHIFT', 'ALT'], 'm') == 'CTRL+ALT+SHIFT+m'
    assert make_trigger([], 'F13') == 'F13'


@pytest.mark.parametrize('trigger, label', [
    ('CTRL+ALT+m', 'Ctrl+Alt+M'),
    ('LOGO+F5', 'Meta+F5'),
    ('CTRL+exclam', 'Ctrl+!'),
    ('CTRL+ALT+plus', 'Ctrl+Alt++'),
    ('ALT+adiaeresis', 'Alt+Ä'),
    ('CTRL+space', 'Ctrl+Space'),
    ('CTRL+Page_Up', 'Ctrl+PgUp'),
    ('XF86AudioMute', 'AudioMute'),
    ('KP_Add', 'KP_Add'),
])
def test_labels(trigger, label):
    assert trigger_label(trigger) == label


@pytest.mark.parametrize('key', ['F1', 'F13', 'F24', 'XF86AudioMute', 'KP_Add', 'Pause', 'Scroll_Lock', 'Print', 'Menu'])
def test_keys_that_type_nothing_work_alone(key):
    assert works_alone(key)


@pytest.mark.parametrize('key', ['m', '1', 'space', 'Return', 'Tab', 'Left', 'Home', 'Delete', 'exclam', 'adiaeresis', 'Fn'])
def test_keys_needed_for_typing_do_not(key):
    assert not works_alone(key)


@pytest.mark.parametrize('keyval, symbol', [
    (Gdk.KEY_exclam, True),
    (Gdk.KEY_1, True),
    (Gdk.KEY_slash, True),
    (Gdk.KEY_m, False),
    (Gdk.KEY_M, False),
    (Gdk.KEY_F5, False),
    (Gdk.KEY_space, False),
    (Gdk.KEY_Return, False),
    (Gdk.KEY_KP_Add, False),
    (Gdk.KEY_Left, False),
])
def test_what_counts_as_a_symbol(keyval, symbol):
    assert is_symbol(keyval) is symbol


def test_modifier_names():
    state = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
    assert set(modifier_names(state)) == {'CTRL', 'SHIFT'}
    assert modifier_names(Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK) == ['LOGO']
    assert modifier_names(Gdk.ModifierType.LOCK_MASK) == []
    assert modifier_names(Gdk.ModifierType(0)) == []
