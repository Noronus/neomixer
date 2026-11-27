'''
Runs the EQ and the effects of a device as a PipeWire filter chain in its own process, the
way pw-loopback runs a route. Its two streams sit between the device and its routes, linked
by hand:

    input:   device monitor -> input.nm_eq_<device> ~ filters ~ output.nm_eq_<device> -> routes
    output:  routes -> input.nm_eq_<device> ~ filters ~ output.nm_eq_<device> -> device

The filters, see _build_graph: noise gate, EQ, loudness normalization, compressor, de-esser,
limiter. Every one is always there, those that are off get parameters that pass the audio
through, so switching one is instant.

Programs can also play straight into a hardware output, and follow it when it is the default
device. Only the session manager can put those through an EQ, so that one is a "smart filter"
of WirePlumber's, which takes every stream headed for its target. See build_config.

The process outlives NeoMixer so audio keeps its EQ with the window closed, and the next
run finds it again by its config path.
'''
import functools
import glob
import os
import re
import hashlib
import logging
import signal
import subprocess
import time

from neomixer.model.eq_model import FILTER_LABELS
from neomixer.model.effects_model import EffectsModel
from neomixer.scripts import pmctl
from neomixer.settings import RUNTIME_DIR

LOG = logging.getLogger('generic')

PREAMP = 'pre'
# Above Nyquist, where PipeWire's lowpass passes everything through
OPEN_LOWPASS_FREQ = 96000.0


def _safe_name(device_name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._-]', '_', device_name)


def eq_nodes(device_name: str) -> tuple[str, str]:
    '''Returns the node names of a device's EQ streams, as (capture, playback).'''
    safe = _safe_name(device_name)
    return f'input.nm_eq_{safe}', f'output.nm_eq_{safe}'


def config_path(device_name: str) -> str:
    return os.path.join(RUNTIME_DIR, f'eq-{_safe_name(device_name)}.conf')


def signature(positions: list[str], eq, smart: bool = False) -> str:
    '''
    Hash of what the filter chain cannot change while running: its channels, filter types,
    whether it measures loudness and how it is connected. Anything else is a runtime parameter.
    '''
    types = ','.join(band.type for band in eq.bands)
    graph = f'{GRAPH_VERSION}|{has_loudness_meter()}'
    return hashlib.sha1(f"{' '.join(positions)}|{types}|{smart}|{graph}".encode()).hexdigest()[:12]


@functools.lru_cache(maxsize=1)
def has_loudness_meter() -> bool:
    '''
    True when PipeWire has its EBU R 128 plugin, which loudness normalization needs. It came
    with PipeWire 1.4 and a distribution can leave it out.
    '''
    dirs = [d for d in os.environ.get('SPA_PLUGIN_DIR', '').split(':') if d]
    dirs += glob.glob('/usr/lib*/spa-0.2') + glob.glob('/usr/lib/*/spa-0.2') + glob.glob('/usr/local/lib*/spa-0.2')
    return any(os.path.exists(os.path.join(d, 'filter-graph', 'libspa-filter-graph-plugin-ebur128.so')) for d in dirs)


def _stream_props(device_name: str, smart: bool) -> tuple[str, str]:
    '''
    Returns the properties of an EQ's capture and playback streams.

    All passive: the EQ never keeps a sound card awake by itself. Virtual: hidden from volume applets.
    '''
    common = 'node.passive = true node.virtual = true stream.dont-remix = true media.category = Filter'
    if not smart:
        linked_by_hand = f'node.autoconnect = false node.dont-reconnect = true {common}'
        return linked_by_hand, linked_by_hand

    filter_props = (
        f'filter.smart = true filter.smart.name = "nm_eq_{_safe_name(device_name)}" '
        f'filter.smart.target = {{ node.name = "{device_name}" }} filter.smart.targetable = false'
    )
    capture = f'media.class = Audio/Sink neomixer.eq.device = "{device_name}" {filter_props} {common}'
    # Without its target this stream must not play into the default device instead, whatever
    # was routed to one output would come out of another. It gets removed, see handle_unplug.
    playback = f'node.dont-fallback = true {filter_props} {common}'
    return capture, playback


#
# # The graph
#
# PipeWire's filters work on one sample or one control value at a time, the dynamics are
# built from them. A detector follows the level of all channels together, so that the stereo
# image stays put:
#
#     per channel: x * x  ->  max of the channels  ->  lowpass (the envelope)  ->  level
#                  ->  gain (linear, clamp)  ->  exp  ->  every channel * gain
#
# Levels and gains are in bels, tenths of the dB the model has: the control values of
# PipeWire's linear filter only go from -10 to 10.
#
# The envelope's lowpass is a biquad in single precision, which stays accurate down to about
# 5 Hz, a time constant of 30 ms. Loudness normalization is slower and measures with the EBU
# R 128 plugin instead, at the rate of control values: once per quantum.
#
GRAPH_VERSION = 2
BEL_BASE = 10 ** (1 / 2)  # exp with this base turns bels into a gain
# No more than this deep below 0 dB, where log would give -inf
LEVEL_FLOOR = 1e-10
# The detectors' envelope, in Hz: higher reacts faster
GATE_SMOOTHING = 15.0
COMPRESSOR_SMOOTHING = 8.0
DEESSER_SMOOTHING = 30.0
LIMITER_SMOOTHING = 30.0
# 1:4, the gate turns down by 3 dB for every dB below its threshold
GATE_EXPANSION = 3.0
GATE_RANGE = 40.0
COMPRESSOR_RANGE = 40.0
DEESSER_RATIO = 4.0
# The shelf starts a bit below where the detector listens, so the highs it hears get all of the cut
DEESSER_SPLIT = 0.75
LIMITER_RANGE = 30.0
# The detector measures power, a sine's peaks are this far above it
CREST = 3.0
# The output clamp of a limiter that is off: 20 dB above full scale, as far as a clamp goes
OPEN_CLAMP = 10.0

# Channel position -> input of the EBU R 128 meter, which weighs surround channels higher
LOUDNESS_PORTS = {
    'FL': 'In FL', 'FR': 'In FR', 'FC': 'In FC', 'MONO': 'In FC',
    'SL': 'In SL', 'SR': 'In SR', 'RL': 'In SL', 'RR': 'In SR',
}
LOUDNESS_UNUSED = ('LFE',)


class _Graph:
    def __init__(self, channels: int):
        self.channels = channels
        self.nodes = []
        self.links = []

    def node(self, name, label, kind='builtin', **extra):
        text = f'{{ type = {kind} name = {name} label = {label}'
        if 'config' in extra:
            text += f' config = {{ {extra["config"]} }}'
        self.nodes.append(text + ' }')
        return name

    def link(self, output, input_):
        self.links.append(f'{{ output = "{output}" input = "{input_}" }}')

    def per_channel_gain(self, name, signals, gain):
        '''Every channel times one gain signal. Returns the new signals.'''
        out = []
        for index, signal_ in enumerate(signals):
            node = self.node(f'{name}_{index + 1}', 'mult')
            self.link(signal_, f'{node}:In 1')
            self.link(gain, f'{node}:In 2')
            out.append(f'{node}:Out')
        return out

    def detector(self, name, signals, smoothing):
        '''
        Gain signal following the level of the loudest channel, see _detector_params.
        '''
        squares = []
        for index, signal_ in enumerate(signals[:8]):
            square = self.node(f'{name}_sq{index + 1}', 'mult')
            self.link(signal_, f'{square}:In 1')
            self.link(signal_, f'{square}:In 2')
            squares.append(square)
        power = self.node(f'{name}_max', 'max')
        for index, square in enumerate(squares):
            self.link(f'{square}:Out', f'{power}:In {index + 1}')
        chain = [
            self.node(f'{name}_env', 'bq_lowpass'),
            self.node(f'{name}_floor', 'linear'),
            self.node(f'{name}_level', 'log'),
            self.node(f'{name}_over', 'linear'),
            self.node(f'{name}_db', 'linear'),
            self.node(f'{name}_clamp', 'clamp'),
            self.node(f'{name}_add', 'linear'),
            self.node(f'{name}_gain', 'exp'),
        ]
        self.link(f'{power}:Out', f'{chain[0]}:In')
        for source, target in zip(chain, chain[1:]):
            self.link(f'{source}:Out', f'{target}:In')
        return f'{chain[-1]}:Out'


def _loudness_port(position, used):
    port = LOUDNESS_PORTS.get(position)
    if position in LOUDNESS_UNUSED or port in used:
        return None
    if port is None:
        # A pro audio card's AUX0, AUX1...: in the order of the front channels
        port = next((p for p in ('In FL', 'In FR', 'In FC', 'In SL', 'In SR') if p not in used), None)
    return port


def _build_graph(positions: list[str], eq, loudness: bool, meter: bool = False) -> _Graph:
    '''
    The graph of a device's filter chain. With "meter" its outputs are not the audio but the
    gain of each effect, in the order of METER_TAPS, see scripts.pw_fxmeter.
    '''
    graph = _Graph(len(positions))
    signals = []
    for index in range(len(positions)):
        graph.node(f'in_{index + 1}', 'copy')
        signals.append(f'in_{index + 1}:Out')

    gate = graph.detector('gate', signals, GATE_SMOOTHING)
    signals = graph.per_channel_gain('gate_out', signals, gate)

    # EQ, a chain of biquads per channel. A high shelf at 0 Hz is a plain gain: the preamp.
    labels = [FILTER_LABELS[band.type] for band in eq.bands] + ['bq_highshelf']
    for index, signal_ in enumerate(signals):
        names = [f'b{band + 1}_{index + 1}' for band in range(len(eq.bands))] + [f'{PREAMP}_{index + 1}']
        for name, label in zip(names, labels):
            graph.node(name, label)
        graph.link(signal_, f'{names[0]}:In')
        for source, target in zip(names, names[1:]):
            graph.link(f'{source}:Out', f'{target}:In')
        signals[index] = f'{names[-1]}:Out'

    if loudness:
        graph.node('loud_meter', 'ebur128', kind='ebur128')
        graph.node('loud_target', 'lufs2gain', kind='ebur128')
        graph.node('loud_clamp', 'clamp')
        used = set()
        for position, signal_ in zip(positions, signals):
            port = _loudness_port(position, used)
            if port is not None:
                used.add(port)
                graph.link(signal_, f'loud_meter:{port}')
        graph.link('loud_meter:Shortterm LUFS', 'loud_target:LUFS')
        graph.link('loud_target:Gain', 'loud_clamp:Control')
        normalized = []
        for index, signal_ in enumerate(signals):
            node = graph.node(f'loud_out_{index + 1}', 'linear')
            graph.link(signal_, f'{node}:In')
            graph.link('loud_clamp:Notify', f'{node}:Mult')
            normalized.append(f'{node}:Out')
        signals = normalized

    compressor = graph.detector('comp', signals, COMPRESSOR_SMOOTHING)
    signals = graph.per_channel_gain('comp_out', signals, compressor)

    # De-esser, a high shelf that follows the highs: out = gain * x + (1 - gain) * lowpass(x).
    # Below the split nothing changes, above it the audio gets the gain.
    highs = []
    for index, signal_ in enumerate(signals):
        node = graph.node(f'ds_hp_{index + 1}', 'bq_highpass')
        graph.link(signal_, f'{node}:In')
        highs.append(f'{node}:Out')
    deesser = graph.detector('ds', highs, DEESSER_SMOOTHING)
    graph.node('ds_rest', 'linear')
    graph.link(deesser, 'ds_rest:In')
    deessed = []
    for index, signal_ in enumerate(signals):
        channel = index + 1
        graph.node(f'ds_lp_{channel}', 'bq_lowpass')
        graph.link(signal_, f'ds_lp_{channel}:In')
        graph.node(f'ds_all_{channel}', 'mult')
        graph.link(signal_, f'ds_all_{channel}:In 1')
        graph.link(deesser, f'ds_all_{channel}:In 2')
        graph.node(f'ds_low_{channel}', 'mult')
        graph.link(f'ds_lp_{channel}:Out', f'ds_low_{channel}:In 1')
        graph.link('ds_rest:Out', f'ds_low_{channel}:In 2')
        graph.node(f'ds_out_{channel}', 'mixer')
        graph.link(f'ds_all_{channel}:Out', f'ds_out_{channel}:In 1')
        graph.link(f'ds_low_{channel}:Out', f'ds_out_{channel}:In 2')
        deessed.append(f'ds_out_{channel}:Out')
    signals = deessed

    limiter = graph.detector('lim', signals, LIMITER_SMOOTHING)
    signals = graph.per_channel_gain('lim_gain', signals, limiter)
    outputs = []
    for index, signal_ in enumerate(signals):
        node = graph.node(f'out_{index + 1}', 'clamp')
        graph.link(signal_, f'{node}:In')
        outputs.append(f'{node}:Out')

    graph.inputs = [f'in_{index + 1}:In' for index in range(len(positions))]
    graph.outputs = outputs
    if meter:
        graph.outputs = _meter_taps(graph, loudness, {
            'gate': gate, 'compressor': compressor, 'deesser': deesser, 'limiter': limiter,
        })
    return graph


METER_TAPS = ('gate', 'normalize', 'compressor', 'deesser', 'limiter')
# Controls of the nodes only a meter has: a constant 1, which normalization's gain multiplies
METER_CONTROLS = {'meter_dc': ['"Mult" = 0', '"Add" = 1'], 'meter_loud': ['"Add" = 0']}


def _meter_taps(graph, loudness, gains):
    '''Graph outputs carrying the gain of each effect, through a copy so a gain can also be an output.'''
    graph.node('meter_dc', 'linear')
    graph.link('in_1:Out', 'meter_dc:In')
    if loudness:
        graph.node('meter_loud', 'linear')
        graph.link('meter_dc:Out', 'meter_loud:In')
        graph.link('loud_clamp:Notify', 'meter_loud:Mult')
        gains['normalize'] = 'meter_loud:Out'
    else:
        gains['normalize'] = 'meter_dc:Out'
    outputs = []
    for name in METER_TAPS:
        node = graph.node(f'tap_{name}', 'copy')
        graph.link(gains[name], f'{node}:In')
        outputs.append(f'{node}:Out')
    return outputs


def _detector_params(name, smoothing, threshold, slope, low, offset=0.0):
    '''
    The gain of a detector, in dB: (level - threshold) * slope, clamped to low..0, plus offset.
    '''
    return [
        (f'{name}_env:Freq', smoothing), (f'{name}_env:Q', 0.5),
        (f'{name}_floor:Mult', 1.0), (f'{name}_floor:Add', LEVEL_FLOOR),
        (f'{name}_level:Base', 10.0), (f'{name}_level:M1', 1.0), (f'{name}_level:M2', 1.0),
        (f'{name}_over:Mult', 1.0), (f'{name}_over:Add', -threshold / 10),
        (f'{name}_db:Mult', slope), (f'{name}_db:Add', 0.0),
        (f'{name}_clamp:Min', low / 10), (f'{name}_clamp:Max', 0.0),
        (f'{name}_add:Mult', 1.0), (f'{name}_add:Add', offset / 10),
        (f'{name}_gain:Base', BEL_BASE),
    ]


def _effects_params(effects, channels: int, loudness: bool, bypass: bool) -> list[tuple[str, float]]:
    '''
    Parameters of the effects. An effect that is off gets a gain clamped to 0 dB.
    '''
    def on(effect):
        return effect.enabled and not bypass

    params = []
    gate = effects.gate
    params += _detector_params('gate', GATE_SMOOTHING, gate.threshold, GATE_EXPANSION, -GATE_RANGE if on(gate) else 0.0)

    if loudness:
        norm = effects.normalize
        params += [('loud_target:Target LUFS', norm.target)]
        if on(norm):
            params += [('loud_clamp:Min', 10 ** (-norm.max_cut / 20)), ('loud_clamp:Max', 10 ** (norm.max_boost / 20))]
        else:
            params += [('loud_clamp:Min', 1.0), ('loud_clamp:Max', 1.0)]

    comp = effects.compressor
    params += _detector_params(
        'comp', COMPRESSOR_SMOOTHING, comp.threshold, -(1 - 1 / comp.ratio),
        -COMPRESSOR_RANGE if on(comp) else 0.0, comp.makeup if on(comp) else 0.0
    )

    deesser = effects.deesser
    for index in range(channels):
        params += [(f'ds_hp_{index + 1}:Freq', deesser.freq), (f'ds_hp_{index + 1}:Q', 0.707),
                   (f'ds_lp_{index + 1}:Freq', deesser.freq * DEESSER_SPLIT), (f'ds_lp_{index + 1}:Q', 0.707),
                   (f'ds_out_{index + 1}:Gain 1', 1.0), (f'ds_out_{index + 1}:Gain 2', 1.0)]
    params += _detector_params(
        'ds', DEESSER_SMOOTHING, deesser.threshold, -(1 - 1 / DEESSER_RATIO), -deesser.max_cut if on(deesser) else 0.0
    )
    params += [('ds_rest:Mult', -1.0), ('ds_rest:Add', 1.0)]

    # Holds the power at the ceiling less a sine's crest, the clamp after it takes what gets through
    limiter = effects.limiter
    params += _detector_params(
        'lim', LIMITER_SMOOTHING, limiter.ceiling - CREST, -1.0, -LIMITER_RANGE if on(limiter) else 0.0
    )
    ceiling = 10 ** (limiter.ceiling / 20) if on(limiter) else OPEN_CLAMP
    for index in range(channels):
        params += [(f'out_{index + 1}:Min', -ceiling), (f'out_{index + 1}:Max', ceiling)]
    return params


def graph_params(eq, effects, channels: int, loudness: bool | None = None) -> list[tuple[str, float]]:
    '''
    Returns the runtime parameters of a device's filter chain as (name, value) pairs. A
    disabled EQ, band or effect keeps running with parameters that pass everything through.
    '''
    if effects is None:
        effects = EffectsModel()
    if loudness is None:
        loudness = has_loudness_meter()
    bypass = effects.bypass
    eq_on = eq.enabled and not bypass

    params = []
    for channel in range(1, channels + 1):
        for index, band in enumerate(eq.bands):
            name = f'b{index + 1}_{channel}'
            freq, gain = band.freq, band.gain
            if not eq_on or not band.enabled:
                gain = 0.0
                if band.type == 'lowpass':
                    freq = OPEN_LOWPASS_FREQ
                elif band.type in ('highpass', 'notch'):
                    freq = 0.0
            params += [(f'{name}:Freq', float(freq)), (f'{name}:Q', float(band.q)), (f'{name}:Gain', float(gain))]
        params += [(f'{PREAMP}_{channel}:Freq', 0.0), (f'{PREAMP}_{channel}:Q', 1.0),
                   (f'{PREAMP}_{channel}:Gain', float(eq.preamp) if eq_on else 0.0)]

    params += _effects_params(effects, channels, loudness, bypass)
    return [(name, float(value)) for name, value in params]


def build_config(device_name: str, positions: list[str], eq, smart: bool = False, effects=None,
                 meter: bool = False) -> str:
    '''
    Returns the config of the PipeWire process that hosts a device's EQ and effects.
        "smart" has the session manager send every stream headed for the device through it
        "meter" makes the copy that reports the effects' gains instead, see scripts.pw_fxmeter
    '''
    loudness = has_loudness_meter()
    graph = _build_graph(positions, eq, loudness, meter)

    controls = {name: list(values) for name, values in METER_CONTROLS.items()} if meter else {}
    for name, value in graph_params(eq, effects, len(positions), loudness):
        node, _, port = name.partition(':')
        controls.setdefault(node, []).append(f'"{port}" = {value:.6g}')
    nodes = []
    for text in graph.nodes:
        name = re.search(r'name = (\S+)', text).group(1)
        if name in controls:
            text = text[:-2] + f' control = {{ {" ".join(controls[name])} }} }}'
        nodes.append(text)

    position_list = ' '.join(positions)
    if meter:
        capture, playback = meter_nodes(device_name)
        common = ('node.autoconnect = false node.dont-reconnect = true node.passive = true node.virtual = true '
                  'stream.dont-remix = true media.category = Filter')
        taps = ' '.join(f'AUX{index}' for index in range(len(METER_TAPS)))
        description = f'NeoMixer effects meter {device_name}'
        capture_props = (f'node.name = "{capture}" audio.channels = {len(positions)} '
                         f'audio.position = [ {position_list} ] {common}')
        playback_props = f'node.name = "{playback}" audio.channels = {len(METER_TAPS)} audio.position = [ {taps} ] {common}'
    else:
        capture, playback = eq_nodes(device_name)
        description = f'NeoMixer EQ {device_name}'
        capture_props, playback_props = _stream_props(device_name, smart)
        capture_props = (f'node.name = "{capture}" neomixer.eq.signature = "{signature(positions, eq, smart)}" '
                         f'{capture_props}')
        playback_props = f'node.name = "{playback}" {playback_props}'
    inputs = ' '.join(f'"{port}"' for port in graph.inputs)
    outputs = ' '.join(f'"{port}"' for port in graph.outputs)
    return f'''# Generated by NeoMixer
context.properties = {{ log.level = 2 }}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt args = {{ nice.level = -11 }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-filter-chain
        args = {{
            node.description = "{description}"
            media.name = "{description}"
            filter.graph = {{
                nodes = [
                    {(chr(10) + '                    ').join(nodes)}
                ]
                links = [
                    {(chr(10) + '                    ').join(graph.links)}
                ]
                inputs = [ {inputs} ]
                outputs = [ {outputs} ]
            }}
            audio.channels = {len(positions)}
            audio.position = [ {position_list} ]
            capture.props = {{ {capture_props} }}
            playback.props = {{ {playback_props} }}
        }}
    }}
]
'''


def meter_nodes(device_name: str) -> tuple[str, str]:
    '''Returns the node names of a device's effects meter, as (capture, playback).'''
    safe = _safe_name(device_name)
    return f'input.nm_fxmeter_{safe}', f'output.nm_fxmeter_{safe}'


def set_params(device_name: str, positions: list[str], eq, effects=None, meter: bool = False) -> bool:
    '''Update a running EQ and its effects, or its meter, without interrupting the audio.'''
    capture = (meter_nodes if meter else eq_nodes)(device_name)[0]
    values = ' '.join(f'"{name}" {value:.6g}' for name, value in graph_params(eq, effects, len(positions)))
    ret = subprocess.run(
        ['pw-cli', 'set-param', capture, 'Props', f'{{ params = [ {values} ] }}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    ).returncode
    if ret != 0:
        LOG.warning('Failed to update the EQ of %s', device_name)
    return ret == 0


def start(device_name: str, positions: list[str], eq, smart: bool = False, effects=None) -> subprocess.Popen:
    '''Write a device's EQ config and start its process.'''
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    path = config_path(device_name)
    with open(path, 'w', encoding='utf-8') as config:
        config.write(build_config(device_name, positions, eq, smart, effects))

    LOG.debug('Starting EQ for %s: %s', device_name, path)
    # its own session: it must survive the terminal or window NeoMixer was started from
    return subprocess.Popen(
        ['pipewire', '-c', path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True
    )


def wait_ready(device_name: str, channels: int, timeout: float = 3.0, interval: float = 0.02) -> bool:
    '''Poll until both streams of a device's EQ have a port per channel.'''
    capture, playback = eq_nodes(device_name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if (len(pmctl.get_ports('input', capture)) >= channels
                    and len(pmctl.get_ports('output', playback)) >= channels):
                return True
        except RuntimeError:
            pass
        time.sleep(interval)
    return False


def wait_linked(device_name: str, timeout: float = 1.0, interval: float = 0.02) -> bool:
    '''Poll until the session manager linked a smart filter to its device.'''
    playback = eq_nodes(device_name)[1]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if any(target.rpartition(':')[0] == device_name for _source, target in pmctl.get_links(playback)):
                return True
        except RuntimeError:
            pass
        time.sleep(interval)
    return False


def running_pids(device_name: str) -> list[int]:
    '''Find the processes running a device's EQ, ours or from an earlier run.'''
    path = config_path(device_name).encode()
    pids = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open(f'/proc/{entry}/cmdline', 'rb') as cmdline:
                argv = cmdline.read().split(b'\0')
        except OSError:
            continue
        if argv[0].endswith(b'pipewire') and path in argv:
            pids.append(int(entry))
    return pids


def running_signature(device_name: str) -> str | None:
    '''Returns the signature an EQ from an earlier run was started with, None without a process.'''
    if not running_pids(device_name):
        return None
    try:
        with open(config_path(device_name), encoding='utf-8') as config:
            match = re.search(r'neomixer\.eq\.signature = "(\w+)"', config.read())
    except OSError:
        return None
    return match.group(1) if match else None


def stop(device_name: str, proc: subprocess.Popen | None = None):
    '''Stop every process running a device's EQ.'''
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    for pid in running_pids(device_name):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    try:
        os.remove(config_path(device_name))
    except OSError:
        pass
