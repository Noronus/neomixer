from typing import Literal

from pydantic import BaseModel, Field, validator

# Filter type -> PipeWire builtin filter label
FILTER_LABELS = {
    'peak': 'bq_peaking',
    'lowshelf': 'bq_lowshelf',
    'highshelf': 'bq_highshelf',
    'lowpass': 'bq_lowpass',
    'highpass': 'bq_highpass',
    'notch': 'bq_notch',
}

FilterType = Literal['peak', 'lowshelf', 'highshelf', 'lowpass', 'highpass', 'notch']

# These filters have no gain, the handle on the curve only moves sideways
GAINLESS_TYPES = ('lowpass', 'highpass', 'notch')

MIN_FREQ = 20.0
MAX_FREQ = 20000.0
MAX_GAIN = 24.0
MIN_Q = 0.1
MAX_Q = 10.0
MAX_PREAMP = 24.0


class EqBand(BaseModel):
    type: FilterType = 'peak'
    freq: float = 1000.0
    gain: float = 0.0
    q: float = 1.0
    enabled: bool = True

    @validator('freq')
    def clamp_freq(cls, value):
        return min(max(value, MIN_FREQ), MAX_FREQ)

    @validator('gain')
    def clamp_gain(cls, value):
        return min(max(value, -MAX_GAIN), MAX_GAIN)

    @validator('q')
    def clamp_q(cls, value):
        return min(max(value, MIN_Q), MAX_Q)


def default_bands() -> list[EqBand]:
    return [
        EqBand(type='lowshelf', freq=60, q=0.7),
        EqBand(freq=150),
        EqBand(freq=350),
        EqBand(freq=800),
        EqBand(freq=2000),
        EqBand(freq=4500),
        EqBand(freq=9000),
        EqBand(type='highshelf', freq=14000, q=0.7),
    ]


class EqModel(BaseModel):
    '''
    Parametric EQ of an input device. Only the running filter's parameters change when
    "enabled" goes off, so that comparing with and without is instant.
    '''
    enabled: bool = False
    preamp: float = 0.0
    bands: list[EqBand] = Field(default_factory=default_bands)
    preset: str = 'Flat'

    @validator('preamp')
    def clamp_preamp(cls, value):
        return min(max(value, -MAX_PREAMP), MAX_PREAMP)

    def is_flat(self) -> bool:
        return self.preamp == 0 and all(
            band.gain == 0 and band.type not in GAINLESS_TYPES or not band.enabled for band in self.bands
        )

    def apply_preset(self, name: str):
        self.preset = name
        self.preamp, self.bands = PRESETS[name]()

    def reset(self):
        self.apply_preset('Flat')


def _gains(*gains, preamp=0.0):
    '''The default bands with these gains'''
    bands = default_bands()
    for band, gain in zip(bands, gains):
        band.gain = float(gain)
    return preamp, bands


def _mic_cleanup():
    preamp, bands = _gains(0, 0, -2, -1, 2, 3, 1, 1)
    bands[0] = EqBand(type='highpass', freq=90, q=0.7)
    return preamp, bands


def _voice_clarity():
    preamp, bands = _gains(0, -2, -3, 0, 3, 4, 1, 0)
    bands[0] = EqBand(type='highpass', freq=70, q=0.7)
    return preamp, bands


PRESETS = {
    'Flat': _gains,
    'Bass Boost': lambda: _gains(7, 4, 1, 0, 0, 0, 0, 0, preamp=-4),
    'Treble Boost': lambda: _gains(0, 0, 0, 0, 1, 3, 4, 6, preamp=-3),
    'Loudness': lambda: _gains(6, 3, 0, -1, 0, 1, 3, 5, preamp=-3),
    'Voice Clarity': _voice_clarity,
    'Mic Cleanup': _mic_cleanup,
    'De-Mud': lambda: _gains(0, -1, -4, -2, 0, 0, 0, 0),
}
