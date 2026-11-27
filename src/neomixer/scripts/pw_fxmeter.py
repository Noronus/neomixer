'''
Shows what the effects of a device do right now. PipeWire reports a filter's parameters, not
the values flowing through it, so a second filter chain, the meter, runs the same graph as the
device's own one, see pw_eq. It gets what goes into that one, from the monitor of its input
stream, and its outputs are the gain of each effect instead of the audio:

    input.nm_eq_<device>:monitor_*  ->  input.nm_fxmeter_<device> ~ same filters ~
        output.nm_fxmeter_<device>:output_AUX0..4  ->  pw-record  ->  callback

The same filters with the same parameters compute the same gains. All of it is passive, it
only runs while the device's audio does, and only while an effects window shows it. Unlike
an EQ it must not outlive NeoMixer: both processes end with it.
'''
import ctypes
import logging
import math
import os
import queue
import re
import signal
import shutil
import struct
import subprocess
import threading
import time

from neomixer.model.connection_model import port_position
from neomixer.scripts import pmctl, pw_eq
from neomixer.settings import RUNTIME_DIR

LOG = logging.getLogger('generic')

RATE = 1000  # the gains change slowly, the recording gets them resampled
BLOCK = 50  # samples per report: 20 a second
TAPS = pw_eq.METER_TAPS
PR_SET_PDEATHSIG = 1


def _die_with_parent():
    '''Runs in the child before exec: the kernel ends it when NeoMixer ends, however that happens.'''
    try:
        ctypes.CDLL('libc.so.6', use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except OSError:
        pass


_spawn_requests = queue.Queue()
_spawner = None
_spawner_lock = threading.Lock()


def _spawn_forever():
    while True:
        args, kwargs, reply = _spawn_requests.get()
        try:
            reply.put(subprocess.Popen(args, preexec_fn=_die_with_parent, **kwargs))
        except Exception as error:  # pylint: disable=broad-except
            reply.put(error)


def _spawn(args, **kwargs) -> subprocess.Popen:
    '''
    Popen from a thread that lives as long as NeoMixer: the kernel ties PR_SET_PDEATHSIG to the
    thread that forked, so a child of a short lived thread would end with that thread.
    '''
    global _spawner  # pylint: disable=global-statement
    with _spawner_lock:
        if _spawner is None:
            _spawner = threading.Thread(target=_spawn_forever, name='fxmeter-spawner', daemon=True)
            _spawner.start()
    reply = queue.Queue(maxsize=1)
    _spawn_requests.put((args, kwargs, reply))
    result = reply.get()
    if isinstance(result, Exception):
        raise result
    return result


def config_path(device_name: str) -> str:
    return os.path.join(RUNTIME_DIR, f'fxmeter-{pw_eq._safe_name(device_name)}.conf')


def recorder_name(device_name: str) -> str:
    return f'nm_fxmeter_rec_{pw_eq._safe_name(device_name)}'


def to_db(gain: float) -> float:
    return 20 * math.log10(gain) if gain > 1e-6 else -120.0


def summarize(frames: list[tuple[float, ...]]) -> dict[str, float]:
    '''
    The gain of each effect over a block, in dB: the most it turned down, for the effects
    that only turn down, and the average of what normalization does.
    '''
    result = {}
    for index, name in enumerate(TAPS):
        values = [frame[index] for frame in frames]
        if name == 'normalize':
            result[name] = to_db(sum(values) / len(values))
        else:
            result[name] = to_db(min(values))
    return result


class EffectsMeter:
    '''
    The meter of one device's effects. "callback" gets a dict of effect name to gain in dB,
    from the reading thread: hand it to the main loop.
    '''

    def __init__(self, device_name: str, callback):
        self.device_name = device_name
        self.callback = callback
        self.positions = None
        self.signature = None
        self._process = None
        self._recorder = None
        self._thread = None
        self._params = None
        self._lock = threading.Lock()
        self._pending = None
        self._worker = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _chain_positions(self):
        '''Channel positions of the device's running filter chain, None without one.'''
        capture = pw_eq.eq_nodes(self.device_name)[0]
        try:
            ports = pmctl.get_ports('output', capture)
        except RuntimeError:
            return None
        monitors = [port for port in ports if port.startswith('monitor_')]
        return [port_position(port) for port in monitors] or None

    def update(self, eq, effects):
        '''
        Follow the device's chain: start, rebuild or relink when it changed, then send the
        parameters. Cheap when nothing changed, call it whenever the settings do and now and then.
        '''
        positions = self._chain_positions()
        if positions is None:
            self.stop()
            return
        signature = pw_eq.signature(positions, eq)
        params = pw_eq.graph_params(eq, effects, len(positions))
        if not self.running or positions != self.positions or signature != self.signature:
            self.stop()
            if not self._start(positions, eq, effects):
                return
            self.positions, self.signature = positions, signature
        elif params != self._params:
            pw_eq.set_params(self.device_name, positions, eq, effects, meter=True)
        self._params = params
        self._link()

    def schedule(self, eq, effects, device_name=None):
        '''
        update() on a thread of its own, as starting takes a moment: the window must not wait.
        Calls while one runs are folded into one more run after it.
        '''
        with self._lock:
            self._pending = (eq, effects, device_name or self.device_name)
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work, name='fxmeter-update', daemon=True)
            self._worker.start()

    def stop_soon(self):
        '''stop() without the window waiting for the processes to end.'''
        with self._lock:
            self._pending = 'stop'
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self._work, name='fxmeter-update', daemon=True)
            self._worker.start()

    def _work(self):
        while True:
            with self._lock:
                pending, self._pending = self._pending, None
                if pending is None:
                    self._worker = None
                    return
            try:
                if pending == 'stop':
                    self.stop()
                    continue
                eq, effects, device_name = pending
                if device_name != self.device_name:
                    self.stop()
                    self.device_name = device_name
                self.update(eq, effects)
            except Exception:  # pylint: disable=broad-except
                LOG.exception('Effects meter of %s failed', self.device_name)

    def _start(self, positions, eq, effects) -> bool:
        pw_record = shutil.which('pw-record')
        if pw_record is None:
            LOG.warning('pw-record is not installed, no effects meter')
            return False
        _stop_leftovers(self.device_name)
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        path = config_path(self.device_name)
        with open(path, 'w', encoding='utf-8') as config:
            config.write(pw_eq.build_config(self.device_name, positions, eq, effects=effects, meter=True))
        self._process = _spawn(
            ['pipewire', '-c', path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        capture, playback = pw_eq.meter_nodes(self.device_name)
        if not _wait_ports(capture, 'input', len(positions)) or not _wait_ports(playback, 'output', len(TAPS)):
            LOG.warning('The effects meter of %s did not start', self.device_name)
            self.stop()
            return False

        # stream.monitor and the name keep it out of the program list, see utils.app_identity
        props = (f'{{ node.name = "{recorder_name(self.device_name)}" node.autoconnect = false node.passive = true '
                 f'stream.monitor = true application.name = "neomixer_fxmeter_peak" }}')
        self._recorder = _spawn(
            [pw_record, '--raw', '--format', 'f32', '--rate', str(RATE), '--channels', str(len(TAPS)),
             '--channel-map', ','.join(f'AUX{index}' for index in range(len(TAPS))), '--latency', '50ms',
             '-P', props, '-'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._thread = threading.Thread(target=self._read, args=(self._recorder,), name='fxmeter', daemon=True)
        self._thread.start()
        return _wait_ports(recorder_name(self.device_name), 'input', len(TAPS))

    def _link(self):
        '''Link what goes into the chain to the meter, and the meter to its recording, unless they are.'''
        chain = pw_eq.eq_nodes(self.device_name)[0]
        capture, playback = pw_eq.meter_nodes(self.device_name)
        try:
            fed = sum(1 for _source, target in pmctl.get_links(chain) if target.startswith(f'{capture}:'))
            recorded = len(pmctl.get_links(playback))
            if fed >= len(self.positions or ()) and recorded >= len(TAPS):
                return
            monitors = [port for port in pmctl.get_ports('output', chain) if port.startswith('monitor_')]
            for source, target in zip(monitors, pmctl.get_ports('input', capture)):
                pmctl.link(f'{chain}:{source}', f'{capture}:{target}', True)
            recorder = recorder_name(self.device_name)
            for source, target in zip(pmctl.get_ports('output', playback), pmctl.get_ports('input', recorder)):
                pmctl.link(f'{playback}:{source}', f'{recorder}:{target}', True)
        except RuntimeError:
            LOG.debug('Could not link the effects meter of %s', self.device_name)

    def _read(self, recorder):
        frame_size = 4 * len(TAPS)
        frames = []
        while True:
            data = recorder.stdout.read(frame_size * BLOCK)
            if not data:
                return
            usable = len(data) - len(data) % frame_size
            for offset in range(0, usable, frame_size):
                frames.append(struct.unpack_from(f'<{len(TAPS)}f', data, offset))
            if len(frames) >= BLOCK:
                try:
                    self.callback(summarize(frames))
                except Exception:  # pylint: disable=broad-except
                    LOG.exception('Effects meter callback failed')
                frames = []

    def stop(self):
        for process in (self._recorder, self._process):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self._recorder = self._process = None
        self.positions = self.signature = self._params = None
        _stop_leftovers(self.device_name)


def _wait_ports(node, direction, count, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if len(pmctl.get_ports(direction, node)) >= count:
                return True
        except RuntimeError:
            pass
        time.sleep(0.02)
    return False


def _stop_leftovers(device_name):
    '''A meter of an earlier run that ended without a chance to stop it.'''
    path = config_path(device_name).encode()
    pattern = re.compile(rb'pipewire$')
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open(f'/proc/{entry}/cmdline', 'rb') as cmdline:
                argv = cmdline.read().split(b'\0')
        except OSError:
            continue
        if pattern.search(argv[0]) and path in argv:
            try:
                os.kill(int(entry), signal.SIGTERM)
            except OSError:
                pass
    try:
        os.remove(config_path(device_name))
    except OSError:
        pass
