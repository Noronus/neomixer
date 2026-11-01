import os

import pytest

from neomixer.utils import desktop_integration

APP = 'org.neomixer.NeoMixer'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_integration, 'XDG_DATA_HOME', str(tmp_path / 'home'))
    return tmp_path / 'home'


def test_installs_the_launcher_for_a_user_install(home, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_DIRS', str(tmp_path / 'system'))
    desktop_integration.install_launcher()
    assert (home / 'applications' / f'{APP}.desktop').exists()
    assert (home / 'icons' / 'hicolor' / 'scalable' / 'apps' / f'{APP}.svg').exists()


def test_a_package_brings_its_own(home, tmp_path, monkeypatch):
    system = tmp_path / 'system'
    (system / 'applications').mkdir(parents=True)
    (system / 'applications' / f'{APP}.desktop').write_text('[Desktop Entry]\n')
    monkeypatch.setenv('XDG_DATA_DIRS', f'/nowhere:{system}')
    desktop_integration.install_launcher()
    assert not os.path.exists(home)
