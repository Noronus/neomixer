'''
The effects of a device besides its EQ. They run in the same PipeWire process as the EQ, see
scripts.pw_eq, in this order:

    noise gate -> EQ -> loudness normalization -> compressor -> de-esser -> limiter

Every effect stays in the running filter when it is switched off and only gets parameters
that pass the audio through, so switching one on or off is instant and has no gap.
'''
from pydantic import BaseModel, Field, validator


def _clamp(low, high):
    return lambda cls, value: min(max(value, low), high)


class GateModel(BaseModel):
    '''Turns down what is quieter than the threshold, like the hiss of a microphone between words.'''
    enabled: bool = False
    threshold: float = -50.0  # dB

    _threshold = validator('threshold', allow_reuse=True)(_clamp(-80.0, -10.0))


class NormalizeModel(BaseModel):
    '''Keeps the loudness at a target, measured over the last 3 seconds as EBU R 128 does.'''
    enabled: bool = False
    target: float = -20.0  # LUFS
    max_boost: float = 12.0  # dB, how far it may turn quiet passages up
    max_cut: float = 24.0  # dB

    _target = validator('target', allow_reuse=True)(_clamp(-36.0, -6.0))
    # PipeWire's clamp goes up to a gain of 10
    _max_boost = validator('max_boost', allow_reuse=True)(_clamp(0.0, 20.0))
    _max_cut = validator('max_cut', allow_reuse=True)(_clamp(0.0, 40.0))


class CompressorModel(BaseModel):
    '''Turns loud moments down: above the threshold the level only rises by 1/ratio.'''
    enabled: bool = False
    threshold: float = -24.0  # dB
    ratio: float = 3.0
    makeup: float = 0.0  # dB

    _threshold = validator('threshold', allow_reuse=True)(_clamp(-60.0, 0.0))
    _ratio = validator('ratio', allow_reuse=True)(_clamp(1.0, 20.0))
    _makeup = validator('makeup', allow_reuse=True)(_clamp(0.0, 24.0))


class DeesserModel(BaseModel):
    '''Turns down only the highs, while they are louder than the threshold: sharp "s" and "sh" sounds.'''
    enabled: bool = False
    freq: float = 6000.0  # Hz, where the turned down highs start
    threshold: float = -30.0  # dB, of the highs alone
    max_cut: float = 12.0  # dB

    _freq = validator('freq', allow_reuse=True)(_clamp(2000.0, 12000.0))
    _threshold = validator('threshold', allow_reuse=True)(_clamp(-60.0, 0.0))
    _max_cut = validator('max_cut', allow_reuse=True)(_clamp(0.0, 24.0))


class LimiterModel(BaseModel):
    '''Keeps the peaks below the ceiling, so nothing clips.'''
    enabled: bool = False
    ceiling: float = -1.0  # dB

    _ceiling = validator('ceiling', allow_reuse=True)(_clamp(-24.0, 0.0))


EFFECT_NAMES = ('gate', 'normalize', 'compressor', 'deesser', 'limiter')


class EffectsModel(BaseModel):
    '''
    "bypass" switches everything off at once, the EQ too, without losing which effects are on:
    the right click on a strip's effects button.
    '''
    bypass: bool = False
    gate: GateModel = Field(default_factory=GateModel)
    normalize: NormalizeModel = Field(default_factory=NormalizeModel)
    compressor: CompressorModel = Field(default_factory=CompressorModel)
    deesser: DeesserModel = Field(default_factory=DeesserModel)
    limiter: LimiterModel = Field(default_factory=LimiterModel)

    def enabled_names(self) -> list[str]:
        return [name for name in EFFECT_NAMES if getattr(self, name).enabled]

    def apply_preset(self, name: str):
        PRESETS[name](self)


def _movie(effects):
    '''Dialog and explosions about as loud as each other'''
    effects.normalize = NormalizeModel(enabled=True, target=-20.0, max_boost=12.0)
    effects.compressor = CompressorModel(enabled=True, threshold=-26.0, ratio=4.0)
    effects.limiter = LimiterModel(enabled=True, ceiling=-1.0)


def _voice(effects):
    '''A microphone: no hiss between words, even level, softer "s" sounds'''
    effects.gate = GateModel(enabled=True, threshold=-50.0)
    effects.compressor = CompressorModel(enabled=True, threshold=-24.0, ratio=3.0, makeup=4.0)
    effects.deesser = DeesserModel(enabled=True)
    effects.limiter = LimiterModel(enabled=True, ceiling=-1.0)


def _music(effects):
    '''Gentle: only keeps peaks from clipping and very loud songs in check'''
    effects.compressor = CompressorModel(enabled=True, threshold=-14.0, ratio=2.0)
    effects.limiter = LimiterModel(enabled=True, ceiling=-1.0)


def _off(effects):
    for name in EFFECT_NAMES:
        getattr(effects, name).enabled = False


PRESETS = {
    'Movie': _movie,
    'Voice': _voice,
    'Music': _music,
    'All off': _off,
}
