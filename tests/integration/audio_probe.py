'''
Plays a sine into one node and measures what comes out of another, inside the sandbox.
'''
import array
import math
import os
import signal
import subprocess
import tempfile
import time
import wave

AMPLITUDE = 0.1
# The session manager restores volumes per media role, and pw-play has one up to PipeWire 1.4. A
# measurement must not get the volume of whichever program set it last.
UNTOUCHED = ['-P', '{ state.restore-props=false }']


def _write_sine(path, freq, seconds, rate=48000):
    samples = array.array('h')
    for index in range(int(seconds * rate)):
        value = int(AMPLITUDE * 32767 * math.sin(2 * math.pi * freq * index / rate))
        samples.extend((value, value))
    with wave.open(path, 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


def _tone_magnitude(path, freq, skip_seconds):
    '''Magnitude of one frequency in the first channel, by the Goertzel algorithm.'''
    with wave.open(path, 'rb') as recorded:
        rate, channels = recorded.getframerate(), recorded.getnchannels()
        samples = array.array('h', recorded.readframes(recorded.getnframes()))[::channels]
    samples = samples[int(skip_seconds * rate):]
    if not samples:
        return 0.0
    coefficient = 2 * math.cos(2 * math.pi * freq / rate)
    previous = before_previous = 0.0
    for sample in samples:
        previous, before_previous = sample / 32768 + coefficient * previous - before_previous, previous
    power = previous ** 2 + before_previous ** 2 - coefficient * previous * before_previous
    return math.sqrt(max(power, 0.0)) * 2 / len(samples)


def measure_db(play_target, record_target, freq, seconds=0.8):
    '''
    Gain in dB from a sink to a sink's output, at one frequency.
    Returns None when nothing arrives.
    '''
    with tempfile.TemporaryDirectory() as folder:
        tone, recording = os.path.join(folder, 'tone.wav'), os.path.join(folder, 'out.wav')
        _write_sine(tone, freq, seconds + 1.5)
        recorder = subprocess.Popen(
            ['pw-record', '--target', record_target, '-P', '{ stream.capture.sink=true }',
             '--rate', '48000', '--channels', '2', '--format', 's16', recording],
            stderr=subprocess.DEVNULL
        )
        time.sleep(0.2)
        player = subprocess.Popen(['pw-play', *UNTOUCHED, '--target', play_target, tone], stderr=subprocess.DEVNULL)
        time.sleep(0.45 + seconds)
        recorder.send_signal(signal.SIGINT)
        recorder.wait()
        player.terminate()
        player.wait()
        magnitude = _tone_magnitude(recording, freq, 0.55)
    if magnitude < 1e-5:
        return None
    return 20 * math.log10(magnitude / AMPLITUDE)


def recorded_magnitude(record_target, freq, seconds=1.2):
    '''What a recording app gets from a source, at one frequency.'''
    with tempfile.TemporaryDirectory() as folder:
        recording = os.path.join(folder, 'out.wav')
        recorder = subprocess.Popen(
            ['pw-record', '--target', record_target, '--rate', '48000', '--channels', '1', '--format', 's16', recording],
            stderr=subprocess.DEVNULL
        )
        time.sleep(seconds)
        recorder.send_signal(signal.SIGINT)
        recorder.wait()
        return _tone_magnitude(recording, freq, 0.3)
