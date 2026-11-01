'''
Frequency response of the EQ, for drawing the curve. Coefficients as PipeWire's builtin
filters compute them (RBJ audio EQ cookbook), checked against the running filters in
tests/integration.
'''
import cmath
import math

RATE = 48000.0


def band_coefficients(filter_type: str, freq: float, gain: float, q: float, rate: float = RATE):
    '''Returns the biquad coefficients of a band as (b0, b1, b2, a0, a1, a2).'''
    w0 = 2 * math.pi * min(freq, rate / 2) / rate
    cos_w0, sin_w0 = math.cos(w0), math.sin(w0)
    alpha = sin_w0 / (2 * q)
    amp = 10 ** (gain / 40)

    if filter_type == 'peak':
        return 1 + alpha * amp, -2 * cos_w0, 1 - alpha * amp, 1 + alpha / amp, -2 * cos_w0, 1 - alpha / amp

    if filter_type == 'lowpass':
        return (1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2, 1 + alpha, -2 * cos_w0, 1 - alpha

    if filter_type == 'highpass':
        return (1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2, 1 + alpha, -2 * cos_w0, 1 - alpha

    if filter_type == 'notch':
        return 1, -2 * cos_w0, 1, 1 + alpha, -2 * cos_w0, 1 - alpha

    shelf_alpha = 2 * math.sqrt(amp) * alpha
    if filter_type == 'lowshelf':
        return (
            amp * ((amp + 1) - (amp - 1) * cos_w0 + shelf_alpha),
            2 * amp * ((amp - 1) - (amp + 1) * cos_w0),
            amp * ((amp + 1) - (amp - 1) * cos_w0 - shelf_alpha),
            (amp + 1) + (amp - 1) * cos_w0 + shelf_alpha,
            -2 * ((amp - 1) + (amp + 1) * cos_w0),
            (amp + 1) + (amp - 1) * cos_w0 - shelf_alpha,
        )

    if filter_type == 'highshelf':
        return (
            amp * ((amp + 1) + (amp - 1) * cos_w0 + shelf_alpha),
            -2 * amp * ((amp - 1) + (amp + 1) * cos_w0),
            amp * ((amp + 1) + (amp - 1) * cos_w0 - shelf_alpha),
            (amp + 1) - (amp - 1) * cos_w0 + shelf_alpha,
            2 * ((amp - 1) - (amp + 1) * cos_w0),
            (amp + 1) - (amp - 1) * cos_w0 - shelf_alpha,
        )

    raise ValueError(f'Unknown filter type: {filter_type}')


def magnitude_db(coefficients, freq: float, rate: float = RATE) -> float:
    '''Gain in dB of a biquad at a frequency.'''
    b0, b1, b2, a0, a1, a2 = coefficients
    z = cmath.exp(-2j * math.pi * freq / rate)
    numerator = abs(b0 + b1 * z + b2 * z * z)
    denominator = abs(a0 + a1 * z + a2 * z * z)
    if numerator <= 0 or denominator <= 0:
        return -120.0
    return max(20 * math.log10(numerator / denominator), -120.0)


def band_response_db(band, freq: float, rate: float = RATE) -> float:
    '''Gain in dB of a single band at a frequency.'''
    if not band.enabled:
        return 0.0
    return magnitude_db(band_coefficients(band.type, band.freq, band.gain, band.q, rate), freq, rate)


def response_db(eq, freq: float, rate: float = RATE) -> float:
    '''Gain in dB of a whole EQ at a frequency, preamp included.'''
    return eq.preamp + sum(band_response_db(band, freq, rate) for band in eq.bands)


def log_frequencies(points: int, low: float = 20.0, high: float = 20000.0) -> list[float]:
    '''Frequencies evenly spaced on a log axis.'''
    ratio = high / low
    return [low * ratio ** (index / (points - 1)) for index in range(points)]
