import asyncio
import time
from unittest import mock

import pytest

from neomixer.controller import event_controller
from neomixer.controller.device_controller import DeviceController
from neomixer.controller.event_controller import EventController
from neomixer.model.config_model import ConfigModel
from neomixer.model.device_model import DeviceModel
from neomixer.repository.device_repository import DeviceRepository
from neomixer.scripts import pmctl, pmctl_async


class _FakePersistence:
    def __init__(self, config):
        self.config = config

    def get_config(self):
        return self.config


def _device(name, primary=False):
    return DeviceModel(name=name, device_type='sink', device_class='virtual', primary=primary,
                       channels=2, channel_list=['FL', 'FR'], selected_channels=[True, True])


@pytest.fixture
def repository():
    config = ConfigModel(devices={'vi': {'1': _device('one', primary=True), '2': _device('two')}, 'hi': {}, 'a': {}, 'b': {}})
    return DeviceRepository(_FakePersistence(config))


@pytest.fixture
def devices(repository):
    return repository.get_device('vi', '1'), repository.get_device('vi', '2')


@pytest.fixture
def controller(repository):
    with mock.patch.object(DeviceController, '__init__', return_value=None):
        device_controller = DeviceController(device_repository=repository)
    device_controller.device_repository = repository
    return device_controller


@pytest.fixture
def set_default():
    with mock.patch.object(pmctl, 'set_primary') as patched:
        yield patched


def test_holding_makes_it_the_primary(controller, devices, set_default):
    one, two = devices
    controller.set_hold_primary('vi', '2', True)
    assert (one.primary, one.hold_primary) == (False, False)
    assert (two.primary, two.hold_primary) == (True, True)
    set_default.assert_called_once_with('sink', 'two')


def test_letting_go_changes_nothing_else(controller, devices, set_default):
    _, two = devices
    controller.set_hold_primary('vi', '2', True)
    set_default.reset_mock()
    controller.set_hold_primary('vi', '2', False)
    assert (two.primary, two.hold_primary) == (True, False)
    set_default.assert_not_called()


def test_the_hold_goes_where_the_star_goes(controller, devices, set_default):
    one, two = devices
    controller.set_hold_primary('vi', '1', True)
    controller.set_primary('vi', '2')
    assert (one.primary, one.hold_primary) == (False, False)
    assert (two.primary, two.hold_primary) == (True, True)


def test_the_star_alone_does_not_hold(controller, devices, set_default):
    _, two = devices
    controller.set_primary('vi', '2')
    assert (two.primary, two.hold_primary) == (True, False)


@pytest.fixture
def listener(repository):
    controller = EventController(repository)
    controller._pulse = mock.Mock()
    yield controller
    controller._async_loop.call_soon_threadsafe(controller._async_loop.stop)


def _run(controller, coroutine):
    return asyncio.run_coroutine_threadsafe(coroutine, controller._async_loop).result(timeout=5)


def _default_is(name):
    return mock.patch.object(pmctl_async, 'get_primary', mock.AsyncMock(
        side_effect=lambda kind, _: _named(name) if kind == 'sink' else None))


def _named(name):
    device = mock.Mock()
    device.name = name
    return device


def test_listener_takes_the_default_back(listener, devices):
    one, _ = devices
    one.hold_primary = True
    with _default_is('steam'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        _run(listener, listener._handle_server_change_event(None))
    restore.assert_awaited_once_with('sink', 'one', listener._pulse)
    assert one.primary is True


def test_listener_leaves_a_held_default_alone(listener, devices):
    one, _ = devices
    one.hold_primary = True
    with _default_is('one'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        _run(listener, listener._handle_server_change_event(None))
    restore.assert_not_awaited()


def test_without_a_hold_the_primary_follows(listener, devices):
    one, two = devices
    with _default_is('two'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        _run(listener, listener._handle_server_change_event(None))
    restore.assert_not_awaited()
    assert (one.primary, two.primary) == (False, True)


def test_someone_insisting_is_answered_at_a_limited_rate(listener, devices, monkeypatch):
    one, _ = devices
    one.hold_primary = True
    monkeypatch.setattr(event_controller, 'HOLD_SECONDS', 0.1)
    with _default_is('steam'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        started = time.monotonic()
        for _ in range(3):
            _run(listener, listener._handle_server_change_event(None))
        elapsed = time.monotonic() - started
        answers = restore.await_count
    assert answers >= 3
    assert elapsed >= (answers - 1) * 0.1 * 0.9


def test_no_waiting_if_the_hold_ended_meanwhile(listener, devices, monkeypatch):
    one, _ = devices
    one.hold_primary = True
    monkeypatch.setattr(event_controller, 'HOLD_SECONDS', 0.2)
    with _default_is('steam'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        _run(listener, listener._handle_server_change_event(None))
        pending = asyncio.run_coroutine_threadsafe(listener._hold_default(one, 'steam'), listener._async_loop)
        one.hold_primary = False
        pending.result(timeout=5)
    assert restore.await_count == 1


def test_a_held_device_that_is_gone_is_not_an_error(listener, devices):
    one, _ = devices
    one.hold_primary = True
    with _default_is('steam'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=False)):
        _run(listener, listener._handle_server_change_event(None))
    assert one.primary is True


def test_a_write_in_the_same_moment_does_not_win(listener, devices, monkeypatch):
    '''It leaves no event behind: the default never changed, to the session manager.'''
    one, _ = devices
    one.hold_primary = True
    monkeypatch.setattr(event_controller, 'HOLD_SECONDS', 0.05)
    with _default_is('steam'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        _run(listener, listener._handle_server_change_event(None))
        assert restore.await_count == 1
        time.sleep(0.2)
        # no event since, and it keeps trying as long as it is not back
        assert restore.await_count > 1

    with _default_is('one'), mock.patch.object(pmctl_async, 'set_primary', mock.AsyncMock(return_value=True)) as restore:
        time.sleep(0.2)
        assert restore.await_count == 0
    assert listener._hold_check is None
