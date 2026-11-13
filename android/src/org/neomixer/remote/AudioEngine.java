package org.neomixer.remote;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.os.Process;
import android.util.Log;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketException;
import java.net.SocketTimeoutException;

/**
 * The phone's side of the audio. The mixer sends what its output "Phone" plays as 16 bit PCM in
 * RTP packets of 5 ms, and takes the microphone the same way.
 *
 * Packets arrive unevenly over a wireless network and the phone's clock is not the PC's, so
 * what was received waits in a buffer that is kept around a target length: played a touch
 * faster when it grows and a touch slower when it shrinks, one sample in 240, which nobody hears.
 */
final class AudioEngine {
    private static final String TAG = "NeoMixerAudio";

    static final int RATE = 48000;
    private static final int CHANNELS = 2;
    /** Frames of one packet, 5 ms. */
    private static final int CHUNK = 240;
    private static final int RTP_HEADER = 12;
    private static final int PAYLOAD_TYPE = 127;
    private static final int MAX_TARGET = RATE * 150 / 1000;
    /** How far the buffer may be off its target before the speed is touched. */
    private static final int DEAD_BAND = CHUNK;
    private static final long NANOS_PER_MS = 1_000_000L;

    /** Frames to keep buffered, by the setting: low delay, balanced, no dropouts. */
    private static final int[] TARGETS = {RATE * 15 / 1000, RATE * 30 / 1000, RATE * 70 / 1000};

    private final Object lock = new Object();
    private final short[] ring = new short[RATE * CHANNELS];
    private int readIndex;
    private int filled;
    private boolean buffering = true;
    private int baseTarget;
    private int target;
    private long lastPacketNanos;
    private long lastUnderrunNanos;

    private volatile boolean running;
    private volatile boolean listen;
    private volatile boolean micWanted;
    private volatile InetAddress micHost;
    private volatile int micPort;
    private DatagramSocket socket;
    private Thread receiver;
    private Thread player;
    private volatile Thread recorder;

    // For the screen
    volatile float playLevel;
    volatile float micLevel;
    volatile int bufferedMs;
    volatile int underruns;
    volatile boolean micFailed;

    AudioEngine(int latency) {
        baseTarget = TARGETS[Math.max(0, Math.min(latency, TARGETS.length - 1))];
        target = baseTarget;
    }

    /** Start receiving and playing, returns the port the mixer has to send to. */
    int start(boolean listen) throws SocketException {
        this.listen = listen;
        socket = new DatagramSocket();
        socket.setSoTimeout(300);
        socket.setReceiveBufferSize(256 * 1024);
        running = true;
        receiver = new Thread(this::receive, "neomixer-rx");
        player = new Thread(this::play, "neomixer-play");
        receiver.start();
        player.start();
        return socket.getLocalPort();
    }

    void stop() {
        running = false;
        setMic(false);
        if (socket != null) {
            socket.close();
        }
        join(receiver);
        join(player);
    }

    void setListen(boolean on) {
        listen = on;
    }

    void setLatency(int latency) {
        synchronized (lock) {
            baseTarget = TARGETS[Math.max(0, Math.min(latency, TARGETS.length - 1))];
            target = baseTarget;
        }
    }

    /** Where the microphone goes. Known once the mixer said its devices are there. */
    void setMicTarget(InetAddress host, int port) {
        micHost = host;
        micPort = port;
    }

    synchronized void setMic(boolean on) {
        micWanted = on;
        if (on && recorder == null && running) {
            micFailed = false;
            recorder = new Thread(this::record, "neomixer-mic");
            recorder.start();
        } else if (!on && recorder != null) {
            Thread thread = recorder;
            recorder = null;
            join(thread);
            micLevel = 0;
        }
    }

    private static void join(Thread thread) {
        if (thread == null || thread == Thread.currentThread()) {
            return;
        }
        try {
            thread.join(1500);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }

    //
    // Receiving
    //
    private void receive() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        byte[] buffer = new byte[4096];
        DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
        int lastSequence = -1;
        long lastNanos = 0;
        while (running) {
            try {
                packet.setLength(buffer.length);
                socket.receive(packet);
            } catch (SocketTimeoutException e) {
                continue;
            } catch (IOException e) {
                break;
            }

            int length = packet.getLength();
            if (length < RTP_HEADER || (buffer[0] & 0xc0) != 0x80) {
                continue;
            }
            int offset = RTP_HEADER + (buffer[0] & 0x0f) * 4;
            if ((buffer[0] & 0x10) != 0 && length >= offset + 4) {
                offset += 4 + (((buffer[offset + 2] & 0xff) << 8) | (buffer[offset + 3] & 0xff)) * 4;
            }
            int samples = (length - offset) / 2;
            if (samples <= 0) {
                continue;
            }

            // A packet that arrives after a later one is too late to play. After a pause the
            // mixer may have started over, and its numbers with it.
            long now = System.nanoTime();
            if (now - lastNanos > 200 * NANOS_PER_MS) {
                lastSequence = -1;
            }
            lastNanos = now;
            int sequence = ((buffer[2] & 0xff) << 8) | (buffer[3] & 0xff);
            if (lastSequence >= 0 && ((sequence - lastSequence) & 0xffff) > 0xffff - 500) {
                continue;
            }
            lastSequence = sequence;

            if (listen) {
                write(buffer, offset, samples);
            }
        }
    }

    private void write(byte[] bytes, int offset, int samples) {
        synchronized (lock) {
            lastPacketNanos = System.nanoTime();
            // Full means nothing was taken for a second: start over from what is new
            if (filled + samples > ring.length) {
                filled = 0;
                buffering = true;
            }
            int index = (readIndex + filled) % ring.length;
            for (int i = 0; i < samples; i++) {
                ring[index] = (short) ((bytes[offset + i * 2] << 8) | (bytes[offset + i * 2 + 1] & 0xff));
                index = index + 1 == ring.length ? 0 : index + 1;
            }
            filled += samples;
        }
    }

    //
    // Playing
    //
    private void play() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        AudioFormat format = new AudioFormat.Builder()
                .setSampleRate(RATE)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                .build();
        int minimum = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT);
        AudioTrack track;
        try {
            track = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build())
                    .setAudioFormat(format)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                    .setBufferSizeInBytes(Math.max(minimum, CHUNK * CHANNELS * 2 * 4))
                    .build();
            track.play();
        } catch (RuntimeException e) {
            Log.e(TAG, "No audio output", e);
            return;
        }
        Log.i(TAG, "Playing, buffer of " + track.getBufferSizeInFrames() + " frames, target " + target);

        short[] taken = new short[(CHUNK + 1) * CHANNELS];
        short[] out = new short[CHUNK * CHANNELS];
        float average = target;
        long lastLog = System.nanoTime();
        try {
            while (running) {
                int frames = take(taken, average);
                if (frames == 0) {
                    java.util.Arrays.fill(out, (short) 0);
                    average = target;
                } else {
                    average += (framesBuffered() - average) * 0.02f;
                    stretch(taken, frames, out);
                }
                playLevel = peak(out, out.length);
                if (track.write(out, 0, out.length, AudioTrack.WRITE_BLOCKING) < 0) {
                    break;
                }

                long now = System.nanoTime();
                if (now - lastLog > 10_000 * NANOS_PER_MS) {
                    lastLog = now;
                    Log.i(TAG, "buffered " + bufferedMs + " ms, target " + target * 1000 / RATE + " ms, underruns " + underruns);
                }
            }
        } finally {
            playLevel = 0;
            try {
                track.pause();
                track.flush();
            } catch (RuntimeException ignored) {
                // it is released next either way
            }
            track.release();
        }
    }

    private int framesBuffered() {
        synchronized (lock) {
            return filled / CHANNELS;
        }
    }

    /**
     * Take the next packet's worth out of the buffer into "taken": one frame more or less than a
     * packet when the buffer is off its target. Returns the frames taken, 0 for silence.
     */
    private int take(short[] taken, float average) {
        synchronized (lock) {
            long now = System.nanoTime();
            int frames = filled / CHANNELS;
            bufferedMs = frames * 1000 / RATE;

            if (buffering && frames >= target) {
                buffering = false;
            }
            if (!buffering && frames < CHUNK + 1) {
                buffering = true;
                // Ran dry while packets were still coming: the network is rougher than the target allows for
                if (now - lastPacketNanos < 100 * NANOS_PER_MS) {
                    underruns++;
                    lastUnderrunNanos = now;
                    target = Math.min(target + CHUNK, MAX_TARGET);
                }
            }
            if (buffering) {
                return 0;
            }

            // Calm for a while: try with less delay again
            if (target > baseTarget && now - lastUnderrunNanos > 60_000 * NANOS_PER_MS) {
                target -= CHUNK;
                lastUnderrunNanos = now;
            }

            // Far too much, as after the phone was busy with something else: skip to where it should be
            if (frames > target * 2 + CHUNK * 8) {
                int skip = frames - target;
                readIndex = (readIndex + skip * CHANNELS) % ring.length;
                filled -= skip * CHANNELS;
            }

            int wanted = CHUNK;
            if (average > target + DEAD_BAND) {
                wanted = CHUNK + 1;
            } else if (average < target - DEAD_BAND) {
                wanted = CHUNK - 1;
            }
            for (int i = 0; i < wanted * CHANNELS; i++) {
                taken[i] = ring[readIndex];
                readIndex = readIndex + 1 == ring.length ? 0 : readIndex + 1;
            }
            filled -= wanted * CHANNELS;
            return wanted;
        }
    }

    /** Fit "frames" frames into one packet's length, by drawing a line between neighbours. */
    private static void stretch(short[] in, int frames, short[] out) {
        if (frames == CHUNK) {
            System.arraycopy(in, 0, out, 0, CHUNK * CHANNELS);
            return;
        }
        float step = (frames - 1) / (float) (CHUNK - 1);
        for (int i = 0; i < CHUNK; i++) {
            float position = i * step;
            int index = Math.min((int) position, frames - 2);
            float fraction = position - index;
            for (int channel = 0; channel < CHANNELS; channel++) {
                int first = in[index * CHANNELS + channel];
                int second = in[(index + 1) * CHANNELS + channel];
                out[i * CHANNELS + channel] = (short) (first + (second - first) * fraction);
            }
        }
    }

    private static float peak(short[] samples, int count) {
        int peak = 0;
        for (int i = 0; i < count; i++) {
            int value = Math.abs(samples[i]);
            if (value > peak) {
                peak = value;
            }
        }
        return peak / 32768f;
    }

    //
    // The microphone
    //
    private void record() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO);
        int minimum = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        AudioRecord record = null;
        try (DatagramSocket sender = new DatagramSocket()) {
            record = new AudioRecord(
                    MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                    Math.max(minimum, CHUNK * 2 * 8));
            record.startRecording();
            if (record.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                throw new IllegalStateException("the microphone did not start");
            }
            Log.i(TAG, "Recording");

            short[] samples = new short[CHUNK];
            byte[] bytes = new byte[RTP_HEADER + CHUNK * 2];
            DatagramPacket packet = new DatagramPacket(bytes, bytes.length);
            int sequence = 0;
            int timestamp = 0;
            int source = (int) System.nanoTime();
            while (running && micWanted && Thread.currentThread() == recorder) {
                int read = record.read(samples, 0, CHUNK, AudioRecord.READ_BLOCKING);
                if (read < 0) {
                    throw new IllegalStateException("reading the microphone failed: " + read);
                }
                InetAddress host = micHost;
                if (host == null || read == 0) {
                    micLevel = 0;
                    continue;
                }

                bytes[0] = (byte) 0x80;
                bytes[1] = (byte) PAYLOAD_TYPE;
                bytes[2] = (byte) (sequence >> 8);
                bytes[3] = (byte) sequence;
                bytes[4] = (byte) (timestamp >> 24);
                bytes[5] = (byte) (timestamp >> 16);
                bytes[6] = (byte) (timestamp >> 8);
                bytes[7] = (byte) timestamp;
                bytes[8] = (byte) (source >> 24);
                bytes[9] = (byte) (source >> 16);
                bytes[10] = (byte) (source >> 8);
                bytes[11] = (byte) source;
                for (int i = 0; i < read; i++) {
                    bytes[RTP_HEADER + i * 2] = (byte) (samples[i] >> 8);
                    bytes[RTP_HEADER + i * 2 + 1] = (byte) samples[i];
                }
                sequence = (sequence + 1) & 0xffff;
                timestamp += read;
                micLevel = peak(samples, read);

                packet.setData(bytes, 0, RTP_HEADER + read * 2);
                packet.setAddress(host);
                packet.setPort(micPort);
                try {
                    sender.send(packet);
                } catch (IOException ignored) {
                    // the network is away for a moment, the next packet may get through
                }
            }
        } catch (RuntimeException | SocketException e) {
            Log.e(TAG, "The microphone stopped", e);
            micFailed = true;
        } finally {
            micLevel = 0;
            if (record != null) {
                try {
                    record.stop();
                } catch (RuntimeException ignored) {
                    // it never started
                }
                record.release();
            }
        }
    }
}
