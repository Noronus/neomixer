import json
import os

from neomixer.model.config_model import ConfigModel
from neomixer.model.known_app import KnownApp
from neomixer.utils.config_persistence import ConfigPersistence, import_legacy_config


def test_save_if_changed_notices_any_change(tmp_path):
    path = tmp_path / 'config.json'
    persistence = ConfigPersistence(ConfigModel, str(path))
    persistence.save()
    assert persistence.save_if_changed() is False

    persistence.get_config().apps['sink_input']['x'] = KnownApp(key='x', label='X', device='media')
    assert persistence.save_if_changed() is True
    assert persistence.save_if_changed() is False
    assert json.loads(path.read_text())['apps']['sink_input']['x']['device'] == 'media'


def test_new_settings_survive_a_reload(tmp_path):
    path = tmp_path / 'config.json'
    persistence = ConfigPersistence(ConfigModel, str(path))
    persistence.get_config().apps['sink_input']['x'] = KnownApp(key='x', label='X', device='media', volume=30, mute=True)
    persistence.save()

    known = ConfigPersistence(ConfigModel, str(path)).get_config().apps['sink_input']['x']
    assert (known.device, known.volume, known.mute) == ('media', 30, True)


def test_unreadable_config_is_kept_before_autosave_overwrites_it(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{ not json')
    persistence = ConfigPersistence(ConfigModel, str(path))
    persistence.save()
    assert (tmp_path / 'config.json.broken').read_text() == '{ not json'
    assert json.loads(path.read_text())


def test_legacy_config_is_imported_once(tmp_path):
    legacy = tmp_path / 'pulsemeeter' / 'config.json'
    legacy.parent.mkdir()
    legacy.write_text('{"vumeters": false, "tray": false, "layout": "Bars"}')
    target = tmp_path / 'neomixer' / 'config.json'

    assert import_legacy_config(str(target), str(legacy)) is True
    config = ConfigPersistence(ConfigModel, str(target))
    assert config.first_run is False
    assert config.get_config().vumeters is False

    legacy.write_text('{"vumeters": true}')
    assert import_legacy_config(str(target), str(legacy)) is False
    assert os.path.exists(target)


def test_legacy_import_needs_a_legacy_config(tmp_path):
    assert import_legacy_config(str(tmp_path / 'a.json'), str(tmp_path / 'missing.json')) is False
