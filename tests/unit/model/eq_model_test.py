import pytest

from neomixer.model.device_model import DeviceModel
from neomixer.model.eq_model import EqModel, EqBand, PRESETS
from neomixer.scripts import pw_eq
from neomixer.utils.eq_math import response_db, band_response_db, log_frequencies


def test_values_are_clamped_to_what_the_filters_can_do():
    band = EqBand(freq=5, gain=99, q=500)
    assert (band.freq, band.gain, band.q) == (20, 24, 10)
    assert EqModel(preamp=-99).preamp == -24


def test_new_device_has_an_eq_that_is_off():
    device = DeviceModel(name='x', device_type='sink', device_class='virtual', primary=False,
                         channels=2, channel_list=['FL', 'FR'], selected_channels=[True, True])
    assert device.eq.enabled is False
    assert device.eq.is_flat()


def test_old_config_without_eq_still_loads():
    device = DeviceModel(**{'name': 'x', 'device_type': 'sink', 'device_class': 'virtual', 'channels': 2,
                            'channel_list': ['FL', 'FR'], 'selected_channels': [True, True]})
    assert len(device.eq.bands) == 8


def test_eq_survives_a_config_roundtrip():
    eq = EqModel(enabled=True, preamp=-3)
    eq.bands[2].gain = 5
    eq.bands[0].type = 'highpass'
    assert EqModel(**eq.model_dump()) == eq


@pytest.mark.parametrize('name', PRESETS)
def test_presets_are_complete(name):
    eq = EqModel()
    eq.apply_preset(name)
    assert eq.preset == name
    assert len(eq.bands) == 8


def test_flat_preset_is_flat():
    eq = EqModel(enabled=True)
    eq.apply_preset('Bass Boost')
    eq.reset()
    assert eq.is_flat()
    assert all(abs(response_db(eq, freq)) < 0.001 for freq in log_frequencies(20))


def test_presets_do_not_share_their_bands():
    first, second = EqModel(), EqModel()
    first.apply_preset('Bass Boost')
    second.apply_preset('Bass Boost')
    first.bands[0].gain = 1
    assert second.bands[0].gain == 7


def test_curve_of_a_peak():
    band = EqBand(freq=1000, gain=6, q=1)
    assert band_response_db(band, 1000) == pytest.approx(6, abs=0.01)
    assert abs(band_response_db(band, 40)) < 0.1


def test_disabled_band_has_no_effect_on_the_curve():
    assert band_response_db(EqBand(gain=6, enabled=False), 1000) == 0


def test_preamp_is_part_of_the_curve():
    assert response_db(EqModel(preamp=-3), 1000) == pytest.approx(-3)


def test_signature_only_changes_for_what_needs_a_rebuild():
    eq = EqModel()
    signature = pw_eq.signature(['FL', 'FR'], eq)
    eq.bands[0].gain = 3
    eq.bands[0].freq = 100
    eq.preamp = 2
    eq.enabled = True
    assert pw_eq.signature(['FL', 'FR'], eq) == signature

    assert pw_eq.signature(['MONO'], eq) != signature
    eq.bands[0].type = 'highpass'
    assert pw_eq.signature(['FL', 'FR'], eq) != signature


def test_params_of_a_disabled_eq_pass_everything_through():
    eq = EqModel(enabled=False, preamp=-6, bands=[
        EqBand(type='peak', gain=9), EqBand(type='lowpass', freq=200), EqBand(type='highpass', freq=200), EqBand(type='notch', freq=200),
    ])
    params = dict(pw_eq.band_params(eq))
    assert params['b1:Gain'] == 0
    assert params['b2:Freq'] > 48000
    assert params['b3:Freq'] == 0
    assert params['b4:Freq'] == 0
    assert params['pre:Gain'] == 0


def test_config_names_a_device_whatever_its_name_contains():
    eq = EqModel()
    config = pw_eq.build_config('bluez_input.80:99:E7', ['MONO'], eq)
    assert 'node.name = "input.nm_eq_bluez_input.80_99_E7"' in config
    assert 'audio.position = [ MONO ]' in config
    assert 'audio.channels = 1' in config
    for label in ('bq_lowshelf', 'bq_peaking', 'bq_highshelf'):
        assert label in config
