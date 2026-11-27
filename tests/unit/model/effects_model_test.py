import re

import pytest

from neomixer.model.device_model import DeviceModel
from neomixer.model.eq_model import EqModel
from neomixer.model.effects_model import EffectsModel, PRESETS, EFFECT_NAMES
from neomixer.scripts import pw_eq


def _params(effects, channels=2, loudness=True):
    return dict(pw_eq.graph_params(EqModel(), effects, channels, loudness))


def _detector_gain_range(params, name):
    return params[f'{name}_clamp:Min'], params[f'{name}_clamp:Max']


def test_old_config_without_effects_still_loads():
    device = DeviceModel(**{'name': 'x', 'device_type': 'sink', 'device_class': 'virtual', 'channels': 2,
                            'channel_list': ['FL', 'FR'], 'selected_channels': [True, True]})
    assert device.effects.enabled_names() == []
    assert device.effects.bypass is False


def test_effects_survive_a_config_roundtrip():
    effects = EffectsModel()
    effects.apply_preset('Movie')
    effects.normalize.target = -16
    assert EffectsModel.parse_raw(effects.json()) == effects


def test_every_preset_runs():
    for name in PRESETS:
        EffectsModel().apply_preset(name)
    effects = EffectsModel()
    effects.apply_preset('Voice')
    effects.apply_preset('All off')
    assert effects.enabled_names() == []


def test_effects_that_are_off_pass_everything_through():
    params = _params(EffectsModel())
    for name in ('gate', 'comp', 'ds', 'lim'):
        assert _detector_gain_range(params, name) == (0, 0)
        assert params[f'{name}_add:Add'] == 0
    assert params['loud_clamp:Min'] == params['loud_clamp:Max'] == 1
    assert params['out_1:Max'] >= 10


def test_bypass_switches_off_everything_and_the_eq():
    effects = EffectsModel(bypass=True)
    for name in EFFECT_NAMES:
        getattr(effects, name).enabled = True
    eq = EqModel(enabled=True, preamp=-6)
    params = dict(pw_eq.graph_params(eq, effects, 2, True))
    assert params['pre_1:Gain'] == 0
    for name in ('gate', 'comp', 'ds', 'lim'):
        assert _detector_gain_range(params, name) == (0, 0)
    assert params['loud_clamp:Max'] == 1


def test_limiter_clamps_at_its_ceiling():
    effects = EffectsModel()
    effects.limiter.enabled = True
    effects.limiter.ceiling = -6
    params = _params(effects)
    assert params['out_2:Max'] == pytest.approx(10 ** (-6 / 20))
    assert params['out_2:Min'] == pytest.approx(-10 ** (-6 / 20))


def test_compressor_slope_follows_the_ratio():
    effects = EffectsModel()
    effects.compressor.enabled = True
    effects.compressor.ratio = 4
    effects.compressor.threshold = -30
    effects.compressor.makeup = 6
    params = _params(effects)
    assert params['comp_db:Mult'] == pytest.approx(-0.75)
    assert params['comp_over:Add'] == pytest.approx(3.0)
    assert params['comp_add:Add'] == pytest.approx(0.6)


def test_normalization_limits_its_boost():
    effects = EffectsModel()
    effects.normalize.enabled = True
    effects.normalize.max_boost = 12
    assert _params(effects)['loud_clamp:Max'] == pytest.approx(10 ** (12 / 20))


def test_every_linear_value_is_in_the_range_pipewire_allows():
    '''PipeWire's linear filter clamps Mult and Add to -10..10, without saying so'''
    effects = EffectsModel()
    for name in EFFECT_NAMES:
        getattr(effects, name).enabled = True
    for threshold in (-80, -60, 0):
        effects.gate.threshold = threshold
        effects.compressor.threshold = max(threshold, -60)
        effects.compressor.ratio = 20
        effects.compressor.makeup = 24
        effects.deesser.threshold = max(threshold, -60)
        effects.limiter.ceiling = max(threshold, -24)
        for name, value in _params(effects).items():
            if re.search(r':(Mult|Add|Min|Max)$', name):
                assert -10 <= value <= 10, (name, value)


def test_config_without_the_loudness_meter_leaves_it_out(monkeypatch):
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: False)
    config = pw_eq.build_config('x', ['FL', 'FR'], EqModel(), effects=EffectsModel())
    assert 'ebur128' not in config
    assert 'loud_' not in config


def test_config_measures_loudness_on_the_right_channels(monkeypatch):
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: True)
    config = pw_eq.build_config('x', ['FL', 'FR', 'FC', 'LFE', 'RL', 'RR'], EqModel(), effects=EffectsModel())
    meter_inputs = re.findall(r'input = "loud_meter:([^"]+)"', config)
    assert meter_inputs == ['In FL', 'In FR', 'In FC', 'In SL', 'In SR']


def test_loudness_meter_is_part_of_the_signature(monkeypatch):
    eq = EqModel()
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: True)
    with_meter = pw_eq.signature(['FL', 'FR'], eq)
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: False)
    assert pw_eq.signature(['FL', 'FR'], eq) != with_meter


def test_every_control_in_the_config_names_a_node_of_the_graph():
    config = pw_eq.build_config('x', ['FL', 'FR'], EqModel(), effects=EffectsModel())
    nodes = set(re.findall(r'name = (\S+) label', config))
    for name, _value in pw_eq.graph_params(EqModel(), EffectsModel(), 2):
        assert name.partition(':')[0] in nodes, name


def test_meter_config_puts_out_one_gain_per_effect(monkeypatch):
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: True)
    config = pw_eq.build_config('in media', ['FL', 'FR'], EqModel(), effects=EffectsModel(), meter=True)
    assert 'node.name = "input.nm_fxmeter_in_media" audio.channels = 2 audio.position = [ FL FR ]' in config
    assert 'node.name = "output.nm_fxmeter_in_media" audio.channels = 5 audio.position = [ AUX0 AUX1 AUX2 AUX3 AUX4 ]' in config
    assert 'outputs = [ "tap_gate:Out" "tap_normalize:Out" "tap_compressor:Out" "tap_deesser:Out" "tap_limiter:Out" ]' in config
    assert '{ output = "loud_clamp:Notify" input = "meter_loud:Mult" }' in config
    # never in the way: nothing links it on its own, and it keeps nothing awake
    assert config.count('node.autoconnect = false') == 2
    assert config.count('node.passive = true') == 2
    assert 'filter.smart' not in config


def test_meter_has_the_same_parameters_as_the_chain(monkeypatch):
    '''set_params sends the chain's parameters to the meter: every one has to name a node there'''
    monkeypatch.setattr(pw_eq, 'has_loudness_meter', lambda: False)
    config = pw_eq.build_config('x', ['MONO'], EqModel(), effects=EffectsModel(), meter=True)
    nodes = set(re.findall(r'name = (\S+) label', config))
    for name, _value in pw_eq.graph_params(EqModel(), EffectsModel(), 1, False):
        assert name.partition(':')[0] in nodes, name
    assert '"tap_normalize:Out"' in config and 'meter_loud' not in config


def test_meter_summary_shows_the_most_each_effect_turned_down():
    from neomixer.scripts import pw_fxmeter
    frames = [(1.0, 2.0, 0.5, 1.0, 1.0), (0.1, 2.0, 0.25, 1.0, 1.0)]
    summary = pw_fxmeter.summarize(frames)
    assert summary['gate'] == pytest.approx(-20)
    assert summary['normalize'] == pytest.approx(6.02, abs=0.01)
    assert summary['compressor'] == pytest.approx(-12.04, abs=0.01)
    assert summary['limiter'] == 0
