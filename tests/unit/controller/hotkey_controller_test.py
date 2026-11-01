import time
from unittest import mock

import pytest

from gi.repository import GLib

from neomixer.controller import hotkey_controller
from neomixer.controller.hotkey_controller import HotkeyController, NOTICE_APPLIED, NOTICE_CANCELLED, NOTICE_FAILED
from neomixer.model.config_model import ConfigModel
from neomixer.model.device_model import DeviceModel
from neomixer.repository.device_repository import DeviceRepository
from neomixer.utils.shortcuts_portal import RESPONSE_CANCELLED, RESPONSE_FAILED, RESPONSE_SUCCESS

UP = 'vi-1-up-CTRL+u'
MUTE = 'vi-1-mute-CTRL+m'


class _FakePersistence:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


class _FakePortal:
    def __init__(self, _app_id, pressed, released, changed, restarted):
        self.pressed, self.released, self.changed, self.restarted = pressed, released, changed, restarted
        self.opened = False
        self.version = 2
        self.session = None
        self.binds = []
        self.dones = []

    def open(self, ready):
        self.opened = True
        ready()

    def bind(self, shortcuts, done):
        self.binds.append(shortcuts)
        self.dones.append(done)

    def answer(self, response, triggers=None):
        self.dones.pop(0)(response, triggers or {})

    def answer_with_keys(self):
        self.answer(RESPONSE_SUCCESS, {shortcut: 'Keys' for shortcut in self.binds[-1]})


def _device(name, **kwargs):
    return DeviceModel(name=name, device_type='sink', device_class='virtual', channels=2, channel_list=['FL', 'FR'],
                       selected_channels=[True, True], **kwargs)


@pytest.fixture
def device():
    return _device('one', hotkeys={'up': 'CTRL+u', 'mute': 'CTRL+m'})


@pytest.fixture
def controller(device):
    config = ConfigModel(devices={'vi': {'1': device, '2': _device('two')}, 'hi': {}, 'a': {}, 'b': {}})
    with mock.patch.object(hotkey_controller, 'ShortcutsPortal', _FakePortal):
        hotkeys = HotkeyController(DeviceRepository(_FakePersistence(config)), mock.Mock(return_value=True))
    hotkeys._release_seen = True
    return hotkeys


@pytest.fixture
def fast_repeat(monkeypatch):
    monkeypatch.setattr(hotkey_controller, 'REPEAT_DELAY_MS', 10)
    monkeypatch.setattr(hotkey_controller, 'REPEAT_EVERY_MS', 5)


def _run(seconds):
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.001)


def test_wanted_has_the_hotkeys_of_all_devices(controller):
    assert set(controller.wanted()) == {UP, MUTE}
    assert controller.wanted()[UP] == ('one: Louder', 'CTRL+u')


def test_without_hotkeys_the_portal_is_left_alone(controller, device):
    device.hotkeys.clear()
    controller.start()
    controller.sync()
    assert controller.portal.opened is False


def test_starting_binds_what_is_in_the_config(controller):
    controller.start()
    assert set(controller.portal.binds[0]) == {UP, MUTE}


def test_a_desktop_without_the_portal_is_not_asked(controller):
    controller.portal.version = 0
    controller.start()
    assert controller.portal.binds == []
    assert controller.available is False


def test_a_second_sync_of_the_same_is_nothing(controller):
    controller.start()
    controller.portal.answer_with_keys()
    controller.sync()
    assert len(controller.portal.binds) == 1


def test_a_sync_waits_for_the_one_in_progress(controller, device):
    controller.start()
    device.hotkeys['down'] = 'CTRL+d'
    controller.sync()
    assert len(controller.portal.binds) == 1
    controller.portal.answer_with_keys()
    assert 'vi-1-down-CTRL+d' in controller.portal.binds[1]


def test_applying_changes_the_config(controller, device):
    controller.apply({('vi', '1', 'up'): 'CTRL+i', ('vi', '1', 'mute'): None, ('vi', '9', 'up'): 'CTRL+x'})
    assert device.hotkeys == {'up': 'CTRL+i'}


def test_an_answer_with_keys_means_they_work(controller):
    controller.start()
    controller.portal.answer_with_keys()
    assert controller.notice == NOTICE_APPLIED
    assert controller.bound == {UP: 'Keys', MUTE: 'Keys'}
    assert controller.busy is False


def test_cancelling_drops_what_was_new_and_keeps_the_rest(controller, device):
    controller.start()
    controller.portal.answer_with_keys()
    controller.apply({('vi', '1', 'down'): 'CTRL+d'})
    controller.portal.answer(RESPONSE_CANCELLED)
    assert device.hotkeys == {'up': 'CTRL+u', 'mute': 'CTRL+m'}
    assert controller.notice == NOTICE_CANCELLED
    # Asked again without it, for the desktop to forget it
    assert set(controller.portal.binds[-1]) == {UP, MUTE}
    controller.portal.answer_with_keys()
    assert controller.notice == NOTICE_CANCELLED


def test_what_the_desktop_has_no_keys_for_is_dropped(controller, device):
    controller.start()
    controller.portal.answer(RESPONSE_SUCCESS, {UP: 'Keys', MUTE: ''})
    assert device.hotkeys == {'up': 'CTRL+u'}
    assert set(controller.portal.binds[-1]) == {UP}


def test_a_failure_keeps_the_config(controller, device):
    controller.start()
    controller.portal.answer(RESPONSE_FAILED)
    assert device.hotkeys == {'up': 'CTRL+u', 'mute': 'CTRL+m'}
    assert controller.notice == NOTICE_FAILED
    assert controller.busy is False


def test_keys_changed_at_the_desktop_are_shown(controller):
    states = []
    controller.connect('state_changed', lambda state: states.append(dict(state.bound)))
    controller.portal.changed({UP: 'Other'})
    assert states == [{UP: 'Other'}]


def test_a_new_portal_gets_them_again(controller):
    controller.start()
    controller.portal.answer_with_keys()
    controller.portal.restarted()
    assert len(controller.portal.binds) == 2


def test_a_press_does_one_step(controller):
    controller.portal.pressed(UP)
    controller.portal.released(UP)
    controller.perform.assert_called_once_with('vi', '1', 'up', False)


def test_an_unknown_shortcut_does_nothing(controller):
    controller.portal.pressed('vi-1-up-CTRL+old')
    controller.portal.pressed('something')
    controller.perform.assert_not_called()


def test_a_held_key_repeats_until_released(controller, fast_repeat):
    controller.portal.pressed(UP)
    _run(0.1)
    assert controller.perform.call_count > 3
    assert controller.perform.call_args_list[1] == mock.call('vi', '1', 'up', True)
    controller.portal.released(UP)
    count = controller.perform.call_count
    _run(0.05)
    assert controller.perform.call_count == count


def test_a_short_press_does_not_repeat(controller, fast_repeat):
    controller.portal.pressed(UP)
    controller.portal.released(UP)
    _run(0.05)
    assert controller.perform.call_count == 1


def test_it_stops_when_there_is_no_further_to_go(controller, fast_repeat):
    controller.perform.side_effect = [True, True, False]
    controller.portal.pressed(UP)
    _run(0.1)
    assert controller.perform.call_count == 3


def test_it_gives_up_on_a_release_that_never_comes(controller, fast_repeat, monkeypatch):
    monkeypatch.setattr(hotkey_controller, 'REPEAT_MAX_SECONDS', 0.05)
    controller.portal.pressed(UP)
    _run(0.1)
    count = controller.perform.call_count
    _run(0.05)
    assert controller.perform.call_count == count


def test_mute_does_not_repeat(controller, fast_repeat):
    controller.portal.pressed(MUTE)
    _run(0.05)
    assert controller.perform.call_count == 1


def test_a_desktop_repeating_a_key_counts_as_one_press(controller):
    for _ in range(5):
        controller.portal.pressed(MUTE)
    assert controller.perform.call_count == 1
    controller.portal.released(MUTE)
    controller.portal.pressed(MUTE)
    assert controller.perform.call_count == 2


def test_no_repeat_until_it_is_known_that_releases_come(controller, fast_repeat):
    '''A key that never reports its release would keep on turning the volume up.'''
    controller._release_seen = False
    controller.portal.pressed(UP)
    _run(0.05)
    assert controller.perform.call_count == 1
    controller.portal.released(UP)
    controller.portal.pressed(UP)
    _run(0.05)
    assert controller.perform.call_count > 2
    controller.portal.released(UP)


@pytest.mark.parametrize('desktop, known', [('KDE', True), ('GNOME', True), ('ubuntu:GNOME', True), ('sway', False), ('', False)])
def test_where_releases_are_known_to_come(desktop, known, monkeypatch):
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', desktop)
    config = ConfigModel(devices={'vi': {}, 'hi': {}, 'a': {}, 'b': {}})
    with mock.patch.object(hotkey_controller, 'ShortcutsPortal', _FakePortal):
        hotkeys = HotkeyController(DeviceRepository(_FakePersistence(config)), mock.Mock())
    assert hotkeys._release_seen is known


def test_stopping_ends_a_repeat(controller, fast_repeat):
    controller.portal.pressed(UP)
    controller.stop()
    _run(0.05)
    assert controller.perform.call_count == 1
