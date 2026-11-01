from unittest import mock

import pytest

from neomixer.utils import autostart


@pytest.fixture
def autostart_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, 'AUTOSTART_DIR', str(tmp_path / 'autostart'))
    return tmp_path / 'autostart'


def test_off_without_an_entry(autostart_dir):
    assert autostart.is_enabled() is False
    assert autostart.starts_hidden() is True


def test_enable_writes_a_valid_entry(autostart_dir):
    with mock.patch.object(autostart, 'launch_command', return_value='/opt/neo\\ mixer'):
        autostart.set_enabled(True)
    assert autostart.is_enabled() is True
    entry = (autostart_dir / 'org.neomixer.NeoMixer.desktop').read_text()
    assert entry.startswith('[Desktop Entry]\n')
    assert 'Type=Application\n' in entry
    assert 'Exec=/opt/neo\\ mixer --hidden\n' in entry


def test_can_start_with_the_window(autostart_dir):
    with mock.patch.object(autostart, 'launch_command', return_value='neomixer'):
        autostart.set_enabled(True, hidden=False)
    assert autostart.starts_hidden() is False
    assert 'Exec=neomixer\n' in (autostart_dir / 'org.neomixer.NeoMixer.desktop').read_text()


def test_disable_removes_the_entry(autostart_dir):
    with mock.patch.object(autostart, 'launch_command', return_value='neomixer'):
        autostart.set_enabled(True)
    autostart.set_enabled(False)
    assert autostart.is_enabled() is False
    autostart.set_enabled(False)


def test_launch_command_points_at_the_running_install(tmp_path, monkeypatch):
    executable = tmp_path / 'neomixer'
    executable.write_text('')
    monkeypatch.setattr('sys.argv', [str(executable)])
    assert autostart.launch_command() == str(executable)


def test_launch_command_falls_back_on_the_module(monkeypatch):
    monkeypatch.setattr('sys.argv', ['something'])
    monkeypatch.setattr('shutil.which', lambda _name: None)
    assert autostart.launch_command().endswith('-m neomixer.main')
