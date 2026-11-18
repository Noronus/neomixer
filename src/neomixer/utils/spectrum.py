'''
Spectrum of what plays on a device: records the device (an output's monitor) with parec and
turns blocks of samples into dB per frequency band.

numpy is used when it is installed, the plain Python FFT is fast enough for the one window
that is open at a time.
'''
import cmath
import logging
import math
import shutil
import struct
import subprocess
import threading

try:
    import numpy
except ImportError:  # pragma: no cover - depends on the system
    numpy = None

LOG = logging.getLogger('generic')

RATE = 48000
SIZE = 4096            # samples per FFT, about 12 Hz per bin
HOP = 2048             # new samples per frame, about 23 frames a second
MIN_FREQ = 20.0
MAX_FREQ = 20000.0
BANDS_PER_OCTAVE = 6
DB_FLOOR = -100.0


def hann(size):
    return [0.5 - 0.5 * math.cos(2 * math.pi * index / (size - 1)) for index in range(size)]


def _fft(values):
    '''Iterative radix-2 FFT of a list of complex numbers, length a power of two.'''
    count = len(values)
    data = list(values)
    j = 0
    for i in range(1, count):
        bit = count >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= count:
        half = length // 2
        step = cmath.exp(-2j * math.pi / length)
        twiddles = [step ** k for k in range(half)]
        for start in range(0, count, length):
            for k in range(half):
                a = data[start + k]
                b = data[start + k + half] * twiddles[k]
                data[start + k] = a + b
                data[start + k + half] = a - b
        length *= 2
    return data


def magnitudes(samples, window):
    '''
    Amplitude per FFT bin (0 .. size/2), scaled so a full scale sine reads 1.0.
    The real input is packed into a half length complex FFT.
    '''
    size = len(samples)
    gain = 2.0 / sum(window)
    if numpy is not None:
        spectrum = numpy.fft.rfft(numpy.asarray(samples, dtype=numpy.float64) * numpy.asarray(window))
        return (numpy.abs(spectrum) * gain).tolist()

    weighted = [s * w for s, w in zip(samples, window)]
    half = size // 2
    packed = _fft([complex(weighted[2 * k], weighted[2 * k + 1]) for k in range(half)])
    result = []
    for k in range(half + 1):
        z = packed[k % half]
        z_conj = packed[(half - k) % half].conjugate()
        even = (z + z_conj) / 2
        odd = (z - z_conj) / 2j
        value = even + cmath.exp(-2j * math.pi * k / size) * odd
        result.append(abs(value) * gain)
    return result


def band_edges(min_freq=MIN_FREQ, max_freq=MAX_FREQ, per_octave=BANDS_PER_OCTAVE):
    '''Center frequencies and edges of log spaced bands: [(low, center, high), ...]'''
    count = int(math.ceil(math.log2(max_freq / min_freq) * per_octave))
    ratio = 2 ** (1 / per_octave)
    bands = []
    for index in range(count):
        center = min_freq * ratio ** (index + 0.5)
        bands.append((center / math.sqrt(ratio), center, center * math.sqrt(ratio)))
    return bands


def to_db(amplitude):
    if amplitude <= 0:
        return DB_FLOOR
    return max(DB_FLOOR, 20 * math.log10(amplitude))


def bands_db(bins, bands, rate=RATE, size=SIZE):
    '''
    Level of each band in dBFS: the loudest bin inside it, so a tone reads the same in a wide
    band as in a narrow one. A band narrower than a bin (low end) takes the bin at its center.
    '''
    resolution = rate / size
    last = len(bins) - 1
    levels = []
    for low, center, high in bands:
        first_bin = int(math.ceil(low / resolution))
        last_bin = int(math.floor(high / resolution))
        if last_bin < first_bin:
            levels.append(to_db(bins[min(round(center / resolution), last)]))
            continue
        levels.append(to_db(max(bins[min(first_bin, last):min(last_bin, last) + 1])))
    return levels


def monitor_name(device_name, pa_device_type):
    '''What to record: the monitor of an output, the device itself for an input.'''
    return f'{device_name}.monitor' if pa_device_type == 'sink' else device_name


class SpectrumAnalyzer:
    '''
    Records a device and calls `callback(levels)` with dB per band for every frame, on its own
    thread. stop() ends the recording.
    '''

    def __init__(self, source, callback, bands=None):
        self.source = source
        self.callback = callback
        self.bands = bands or band_edges()
        self.window = hann(SIZE)
        self._process = None
        self._thread = None
        self._stopped = threading.Event()

    def start(self):
        parec = shutil.which('parec')
        if parec is None:
            LOG.warning('parec is not installed, no spectrum')
            return False
        # The client name ends like the meters' do, so the recording stays out of the app list
        self._process = subprocess.Popen(
            [
                parec, '-d', self.source, '--format=float32le', '--channels=1', f'--rate={RATE}',
                '--latency-msec=20', '--client-name=neomixer_spectrum_peak', '--stream-name=peak detect',
                '--property=stream.monitor=true',
            ],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self._thread = threading.Thread(target=self._run, name='spectrum', daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stopped.set()
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def _run(self):
        process = self._process
        samples = [0.0] * SIZE
        unpack = struct.Struct(f'<{HOP}f').unpack
        block = HOP * 4
        while not self._stopped.is_set():
            data = process.stdout.read(block)
            if len(data) < block:
                break
            samples = samples[HOP:] + list(unpack(data))
            try:
                self.callback(bands_db(magnitudes(samples, self.window), self.bands))
            except Exception:
                LOG.exception('Spectrum callback failed')
                break
        LOG.debug('Spectrum of %s ended', self.source)
