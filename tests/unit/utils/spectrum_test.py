import math

import pytest

from neomixer.utils import spectrum


@pytest.fixture(params=['python', 'numpy'])
def backend(request, monkeypatch):
    if request.param == 'python':
        monkeypatch.setattr(spectrum, 'numpy', None)
    elif spectrum.numpy is None:
        pytest.skip('numpy not installed')


def _sine(freq, amplitude=1.0):
    return [amplitude * math.sin(2 * math.pi * freq * i / spectrum.RATE) for i in range(spectrum.SIZE)]


def _loudest(levels, bands):
    index = max(range(len(levels)), key=levels.__getitem__)
    return bands[index], levels[index]


@pytest.mark.parametrize('freq', [50, 440, 1000, 8000])
def test_full_scale_sine_reads_near_zero_db_in_its_band(backend, freq):
    bands = spectrum.band_edges()
    levels = spectrum.bands_db(spectrum.magnitudes(_sine(freq), spectrum.hann(spectrum.SIZE)), bands)
    _, loudest = _loudest(levels, bands)
    own = next(level for (low, _, high), level in zip(bands, levels) if low <= freq < high)
    # in the bass a band is narrower than an FFT bin, a neighbour may read as loud
    assert own == pytest.approx(loudest, abs=0.01)
    assert -1.5 < own <= 0.1


def test_half_amplitude_is_six_db_lower(backend):
    bands = spectrum.band_edges()
    window = spectrum.hann(spectrum.SIZE)
    full = max(spectrum.bands_db(spectrum.magnitudes(_sine(1000), window), bands))
    half = max(spectrum.bands_db(spectrum.magnitudes(_sine(1000, 0.5), window), bands))
    assert full - half == pytest.approx(6.02, abs=0.05)


def test_silence_reads_the_floor(backend):
    bands = spectrum.band_edges()
    levels = spectrum.bands_db(spectrum.magnitudes([0.0] * spectrum.SIZE, spectrum.hann(spectrum.SIZE)), bands)
    assert set(levels) == {spectrum.DB_FLOOR}


def test_python_fft_matches_numpy():
    numpy = pytest.importorskip('numpy')
    samples = [math.sin(i * 0.37) + 0.3 * math.cos(i * 1.9) for i in range(spectrum.SIZE)]
    window = spectrum.hann(spectrum.SIZE)
    expected = spectrum.magnitudes(samples, window)
    original = spectrum.numpy
    try:
        spectrum.numpy = None
        actual = spectrum.magnitudes(samples, window)
    finally:
        spectrum.numpy = original
    assert numpy.allclose(actual, expected, atol=1e-9)


def test_bands_cover_the_audible_range():
    bands = spectrum.band_edges()
    assert bands[0][0] == pytest.approx(20)
    assert bands[-1][2] >= 20000


def test_monitor_name():
    assert spectrum.monitor_name('nm_out', 'sink') == 'nm_out.monitor'
    assert spectrum.monitor_name('mic', 'source') == 'mic'


def test_recorder_stays_out_of_the_app_list():
    from neomixer.utils.app_identity import is_app_stream, is_system_client
    props = {'application.name': 'neomixer_spectrum_peak', 'application.process.binary': 'parec',
             'media.name': 'peak detect', 'stream.monitor': 'true'}
    assert not is_app_stream(props, 'peak detect')
    assert is_system_client(props)
