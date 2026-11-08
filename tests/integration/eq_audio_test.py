'''
Measures real audio through the real filter chain. Needs tests/integration/sandbox.sh running:
    source .sandbox/env && python -m pytest tests/integration/eq_audio_test.py
'''
import glob
import os
import subprocess
import time
import unittest

from tests.integration.audio_probe import measure_db, recorded_magnitude
from neomixer.model.eq_model import EqModel, EqBand
from neomixer.scripts import pw_eq, pmctl
from neomixer.utils.eq_math import response_db

IN_SANDBOX = 'NEOMIXER_SANDBOX' in os.environ
SOURCE, TARGET = 'eqtest_in', 'eqtest_out'


def _link(state=True):
    capture, playback = pw_eq.eq_nodes(SOURCE)
    for channel in ('FL', 'FR'):
        pmctl.link(f'{SOURCE}:monitor_{channel}', f'{capture}:input_{channel}', state)
        pmctl.link(f'{playback}:output_{channel}', f'{TARGET}:playback_{channel}', state)


@unittest.skipUnless(IN_SANDBOX, 'needs the audio sandbox')
class EqAudioTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        for name in (SOURCE, TARGET):
            pmctl.create_device('sink', name, 2, ['FL', 'FR'])
        pmctl.wait_for_ports({SOURCE: 2, TARGET: 2})

    @classmethod
    def tearDownClass(cls):
        for name in (SOURCE, TARGET):
            pmctl.remove_device(name)

    def setUp(self):
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            pw_eq.stop(SOURCE, process)
        time.sleep(0.1)

    def start(self, eq):
        process = pw_eq.start(SOURCE, ['FL', 'FR'], eq)
        self.processes.append(process)
        self.assertTrue(pw_eq.wait_ready(SOURCE, 2))
        _link()

    def assert_matches_curve(self, eq, freq, tolerance=0.35):
        expected = response_db(eq, freq)
        measured = measure_db(SOURCE, TARGET, freq)
        if expected < -60:
            self.assertTrue(measured is None or measured < -40, f'{measured} dB at {freq} Hz')
            return
        self.assertIsNotNone(measured, f'no audio at {freq} Hz')
        self.assertAlmostEqual(measured, expected, delta=tolerance, msg=f'{freq} Hz')

    def test_every_filter_type_matches_the_drawn_curve(self):
        cases = [
            EqBand(type='peak', freq=1000, gain=9, q=1.0),
            EqBand(type='peak', freq=1000, gain=-9, q=3.0),
            EqBand(type='lowshelf', freq=300, gain=8, q=0.7),
            EqBand(type='highshelf', freq=3000, gain=-8, q=0.7),
            EqBand(type='lowpass', freq=1000, q=0.7),
            EqBand(type='highpass', freq=1000, q=0.7),
            EqBand(type='lowpass', freq=2000, q=2.0),
            EqBand(type='highpass', freq=500, q=2.0),
            EqBand(type='notch', freq=1000, q=1.0),
        ]
        for band in cases:
            with self.subTest(band=band):
                eq = EqModel(enabled=True, bands=[band])
                self.start(eq)
                for freq in (band.freq / 2, band.freq, band.freq * 2):
                    self.assert_matches_curve(eq, freq)
                pw_eq.stop(SOURCE, self.processes.pop())
                time.sleep(0.1)

    def test_runtime_changes_apply_without_restart(self):
        eq = EqModel(enabled=True, bands=[EqBand(freq=1000, gain=0)])
        self.start(eq)
        pid = self.processes[0].pid
        self.assertAlmostEqual(measure_db(SOURCE, TARGET, 1000), 0, delta=0.2)

        eq.bands[0].gain = 12
        self.assertTrue(pw_eq.set_params(SOURCE, eq))
        self.assertAlmostEqual(measure_db(SOURCE, TARGET, 1000), 12, delta=0.2)

        eq.bands[0].freq = 4000
        self.assertTrue(pw_eq.set_params(SOURCE, eq))
        self.assert_matches_curve(eq, 1000)
        self.assert_matches_curve(eq, 4000)

        eq.preamp = -6
        self.assertTrue(pw_eq.set_params(SOURCE, eq))
        self.assert_matches_curve(eq, 4000)

        self.assertEqual(pw_eq.running_pids(SOURCE), [pid])

    def test_disabled_eq_passes_everything_through(self):
        bands = [
            EqBand(type='peak', freq=1000, gain=12),
            EqBand(type='lowshelf', freq=1000, gain=12),
            EqBand(type='highshelf', freq=1000, gain=12),
            EqBand(type='lowpass', freq=200),
            EqBand(type='highpass', freq=8000),
            EqBand(type='notch', freq=1000),
        ]
        eq = EqModel(enabled=True, preamp=-3, bands=bands)
        self.start(eq)
        self.assertLess(measure_db(SOURCE, TARGET, 1000) or -120, -6)

        eq.enabled = False
        self.assertTrue(pw_eq.set_params(SOURCE, eq))
        for freq in (100, 1000, 10000):
            self.assertAlmostEqual(measure_db(SOURCE, TARGET, freq), 0, delta=0.2, msg=f'{freq} Hz')

    def test_disabled_band_passes_through(self):
        eq = EqModel(enabled=True, bands=[EqBand(type='lowpass', freq=200, enabled=False), EqBand(freq=1000, gain=6)])
        self.start(eq)
        self.assertAlmostEqual(measure_db(SOURCE, TARGET, 1000), 6, delta=0.2)

    def test_finds_a_process_from_an_earlier_run(self):
        eq = EqModel(enabled=True)
        self.start(eq)
        self.assertEqual(pw_eq.running_signature(SOURCE), pw_eq.signature(['FL', 'FR'], eq))
        self.assertEqual(pw_eq.running_pids(SOURCE), [self.processes[0].pid])

        pw_eq.stop(SOURCE, self.processes.pop())
        time.sleep(0.1)
        self.assertEqual(pw_eq.running_pids(SOURCE), [])
        self.assertIsNone(pw_eq.running_signature(SOURCE))

    def test_recording_app_pulls_a_microphone_through_the_eq(self):
        '''
        Nothing plays here, the app recording is all that keeps this chain running. The EQ's
        nodes are passive, so this has to work without them activating anything themselves.
        '''
        plugin = 'spa-0.2/audiotestsrc/libspa-audiotestsrc.so'
        if not glob.glob(f'/usr/lib*/{plugin}') + glob.glob(f'/usr/lib/*/{plugin}'):
            self.skipTest('this PipeWire comes without the test source, Fedora has none')

        mic, bus = 'eqtest_mic', 'eqtest_bus'
        subprocess.run(['pw-cli', 'create-node', 'adapter', (
            f'{{ factory.name=audiotestsrc node.name={mic} node.description={mic} '
            'media.class=Audio/Source object.linger=true node.driver=false }'
        )], check=True, stdout=subprocess.DEVNULL)
        self.addCleanup(subprocess.run, ['pw-cli', 'destroy', mic], check=False, stdout=subprocess.DEVNULL)
        pmctl.create_device('source', bus, 1, ['MONO'])
        self.addCleanup(pmctl.remove_device, bus)
        self.assertTrue(pmctl.wait_for_device_port('output', mic))
        self.assertTrue(pmctl.wait_for_device_port('input', bus))
        port = pmctl.get_ports('output', mic)[0]

        pmctl.link(f'{mic}:{port}', f'{bus}:input_MONO')
        direct = recorded_magnitude(bus, 440)
        self.assertGreater(direct, 0.5)
        pmctl.link(f'{mic}:{port}', f'{bus}:input_MONO', False)

        eq = EqModel(enabled=True, preamp=-6)
        process = pw_eq.start(mic, ['MONO'], eq)
        self.addCleanup(pw_eq.stop, mic, process)
        self.assertTrue(pw_eq.wait_ready(mic, 1))
        capture, playback = pw_eq.eq_nodes(mic)
        pmctl.link(f'{mic}:{port}', f'{capture}:input_MONO')
        pmctl.link(f'{playback}:output_MONO', f'{bus}:input_MONO')
        self.assertAlmostEqual(recorded_magnitude(bus, 440), direct / 2, delta=direct * 0.05)

    def test_idle_eq_does_not_keep_the_graph_running(self):
        self.start(EqModel(enabled=True))
        time.sleep(0.5)
        states = subprocess.check_output(['pactl', 'list', 'short', 'sinks'], text=True)
        for line in states.splitlines():
            if SOURCE in line or TARGET in line:
                self.assertNotIn('RUNNING', line)


if __name__ == '__main__':
    unittest.main()
