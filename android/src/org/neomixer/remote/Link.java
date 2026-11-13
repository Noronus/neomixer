package org.neomixer.remote;

import android.content.Context;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * The connection to the mixer: finds it, proves who this phone is, and keeps talking to it for
 * as long as anything on the phone needs it. One JSON object per line, see remote_controller.py
 * on the other side.
 *
 * Fields are read on the main thread, where the listeners are called too.
 */
final class Link {
    private static final String TAG = "NeoMixerLink";

    static final int DEFAULT_PORT = 47800;
    private static final byte[] DISCOVERY_QUESTION = "NEOMIXER?".getBytes(StandardCharsets.US_ASCII);
    private static final int CONNECT_TIMEOUT_MS = 3000;
    private static final int PING_MS = 3000;
    /** The mixer answers every ping, so this much silence is a dead connection. */
    private static final int SILENCE_TIMEOUT_MS = 9000;
    /** A finger on a fader changes it far more often than is worth sending. */
    private static final int COALESCE_MS = 30;

    enum Status { SEARCHING, CONNECTING, PAIRING, CONNECTED, DENIED }

    interface Listener {
        /** Status, the mixer's name, or the audio changed. */
        void onLinkChanged();

        void onState(MixerState state);
    }

    private static Link instance;

    static synchronized Link get(Context context) {
        if (instance == null) {
            instance = new Link(context.getApplicationContext());
        }
        return instance;
    }

    private final Context context;
    private final Prefs prefs;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Handler sender;
    private final List<Listener> listeners = new CopyOnWriteArrayList<>();
    private final Object wake = new Object();
    private final Map<String, JSONObject> pending = new HashMap<>();

    // Read on the main thread
    Status status = Status.SEARCHING;
    String mixerName = "";
    String host = "";
    MixerState state;
    boolean audioOn;
    int micPort;
    String audioError;

    private final Set<Object> users = new LinkedHashSet<>();
    private Thread thread;
    /** The thread that is the connection. One that was let go finds it is no longer this. */
    private volatile Thread current;
    private volatile Socket socket;
    private volatile OutputStream out;
    private volatile boolean welcomed;
    private volatile boolean retry;
    private volatile int audioPort;

    private Link(Context context) {
        this.context = context;
        prefs = new Prefs(context);
        HandlerThread thread = new HandlerThread("neomixer-send");
        thread.start();
        sender = new Handler(thread.getLooper());
    }

    void addListener(Listener listener) {
        listeners.add(listener);
    }

    void removeListener(Listener listener) {
        listeners.remove(listener);
    }

    /** Keep the connection for "user", which is the screen or the audio. Without any it is closed. */
    synchronized void acquire(Object user) {
        users.add(user);
        if (thread == null) {
            thread = new Thread(this::run, "neomixer-link");
            current = thread;
            thread.start();
        }
    }

    synchronized void release(Object user) {
        users.remove(user);
        if (users.isEmpty() && thread != null) {
            current = null;
            thread = null;
            welcomed = false;
            closeSocket();
            synchronized (wake) {
                wake.notifyAll();
            }
        }
    }

    /** After a new address was typed in, or to ask a mixer again that said no. */
    void reconnect() {
        retry = true;
        closeSocket();
        synchronized (wake) {
            wake.notifyAll();
        }
    }

    //
    // What the phone asks for
    //
    void setVolume(MixerState.Strip strip, int volume) {
        coalesce("v" + strip.key(), message("volume").put("type", strip.type).put("id", strip.id).put("value", volume));
    }

    void setMute(MixerState.Strip strip, boolean mute) {
        send(message("mute").put("type", strip.type).put("id", strip.id).put("value", mute));
    }

    void setRoute(MixerState.Strip input, MixerState.Strip output, boolean on) {
        send(message("route").put("in_type", input.type).put("in_id", input.id)
                .put("out_type", output.type).put("out_id", output.id).put("value", on));
    }

    void setAppVolume(MixerState.App app, int volume) {
        coalesce("a" + app.key(), message("app_volume").put("app_type", app.appType).put("key", app.key).put("value", volume));
    }

    void setAppMute(MixerState.App app, boolean mute) {
        send(message("app_mute").put("app_type", app.appType).put("key", app.key).put("value", mute));
    }

    /**
     * Have the mixer play to "port" of this phone and listen for its microphone, 0 to stop. It
     * is asked again whenever the connection comes back.
     */
    void setAudio(int port) {
        audioPort = port;
        if (port == 0) {
            main.post(() -> {
                audioOn = false;
                audioError = null;
                notifyChanged();
            });
        }
        if (welcomed) {
            sendAudio();
        }
    }

    private void sendAudio() {
        int port = audioPort;
        send(message("audio").put("on", port != 0).put("port", port));
    }

    /** A JSONObject whose put() does not need catching: the keys are constants and never null. */
    private static final class Message extends JSONObject {
        @Override
        public Message put(String name, Object value) {
            try {
                super.put(name, value);
            } catch (JSONException e) {
                throw new IllegalArgumentException(e);
            }
            return this;
        }

        @Override
        public Message put(String name, int value) {
            return put(name, (Object) value);
        }

        @Override
        public Message put(String name, boolean value) {
            return put(name, (Object) value);
        }
    }

    private static Message message(String kind) {
        return new Message().put("t", kind);
    }

    private void coalesce(String key, JSONObject message) {
        synchronized (pending) {
            boolean scheduled = !pending.isEmpty();
            pending.put(key, message);
            if (!scheduled) {
                sender.postDelayed(this::flushPending, COALESCE_MS);
            }
        }
    }

    private void flushPending() {
        List<JSONObject> messages;
        synchronized (pending) {
            messages = new ArrayList<>(pending.values());
            pending.clear();
        }
        for (JSONObject message : messages) {
            write(message);
        }
    }

    private void send(JSONObject message) {
        sender.post(() -> {
            // What was held back for a fader comes first, or a mute could overtake it
            flushPending();
            write(message);
        });
    }

    /** On the sender thread only. */
    private void write(JSONObject message) {
        OutputStream stream = out;
        if (stream == null) {
            return;
        }
        try {
            stream.write((message.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            stream.flush();
        } catch (IOException e) {
            closeSocket();
        }
    }

    //
    // The connection
    //
    private boolean running() {
        return Thread.currentThread() == current;
    }

    private void run() {
        while (running()) {
            try {
                setStatus(Status.SEARCHING, "");
                InetSocketAddress address = locate();
                if (address == null) {
                    pause(400);
                    continue;
                }
                converse(address);
            } catch (IOException | JSONException | RuntimeException e) {
                Log.i(TAG, "Connection ended: " + e);
            } finally {
                if (running()) {
                    welcomed = false;
                    closeSocket();
                    main.post(() -> {
                        audioOn = false;
                        notifyChanged();
                    });
                }
            }

            if (denied && running()) {
                // Asking again by itself would only open the question on the PC over and over
                synchronized (wake) {
                    while (running() && !retry) {
                        try {
                            wake.wait();
                        } catch (InterruptedException e) {
                            return;
                        }
                    }
                }
                denied = false;
            }
            retry = false;
            pause(800);
        }
    }

    private volatile boolean denied;

    private void converse(InetSocketAddress address) throws IOException, JSONException {
        if (!running()) {
            return;
        }
        String hostName = address.getAddress().getHostAddress();
        setStatus(Status.CONNECTING, hostName);

        Socket connection = new Socket();
        socket = connection;
        connection.connect(address, CONNECT_TIMEOUT_MS);
        connection.setTcpNoDelay(true);
        connection.setSoTimeout(SILENCE_TIMEOUT_MS);
        out = connection.getOutputStream();
        BufferedReader in = new BufferedReader(new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8), 65536);

        send(message("hello").put("v", 1).put("id", prefs.phoneId()).put("name", Prefs.phoneName(context)));
        sender.postDelayed(new Runnable() {
            @Override
            public void run() {
                if (socket == connection && !connection.isClosed()) {
                    send(message("ping"));
                    sender.postDelayed(this, PING_MS);
                }
            }
        }, PING_MS);

        String line;
        while (running() && (line = in.readLine()) != null) {
            JSONObject message = new JSONObject(line);
            switch (message.optString("t")) {
                case "challenge":
                    send(message("auth").put("mac", answerTo(message.getString("nonce"))));
                    break;
                case "pair":
                    // Someone has to walk to the PC and click: that takes as long as it takes
                    connection.setSoTimeout(0);
                    setStatus(Status.PAIRING, hostName);
                    send(message("pair").put("secret", prefs.secret()));
                    break;
                case "welcome":
                    connection.setSoTimeout(SILENCE_TIMEOUT_MS);
                    prefs.setLastHost(hostName + ":" + address.getPort());
                    welcomed = true;
                    String name = message.optString("name", hostName);
                    main.post(() -> mixerName = name);
                    setStatus(Status.CONNECTED, hostName);
                    if (audioPort != 0) {
                        sendAudio();
                    }
                    break;
                case "denied":
                    denied = true;
                    setStatus(Status.DENIED, hostName);
                    return;
                case "state":
                    MixerState parsed = MixerState.parse(message);
                    main.post(() -> {
                        state = parsed;
                        for (Listener listener : listeners) {
                            listener.onState(parsed);
                        }
                    });
                    break;
                case "audio":
                    boolean on = message.optBoolean("on");
                    int port = message.optInt("mic_port");
                    String error = message.has("error") ? message.optString("error") : null;
                    main.post(() -> {
                        audioOn = on;
                        micPort = port;
                        audioError = error;
                        notifyChanged();
                    });
                    break;
                default:
                    break;
            }
        }
    }

    private String answerTo(String nonce) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(Prefs.unhex(prefs.secret()), "HmacSHA256"));
            return Prefs.hex(mac.doFinal(Prefs.unhex(nonce)));
        } catch (java.security.GeneralSecurityException | RuntimeException e) {
            return "";
        }
    }

    /** Where the mixer is: the address typed in, or whoever answers the question shouted into the network. */
    private InetSocketAddress locate() {
        String typed = prefs.host();
        if (!typed.isEmpty()) {
            InetSocketAddress address = parse(typed);
            if (address == null) {
                pause(1500);
            }
            return address;
        }

        InetSocketAddress found = discover();
        if (found != null) {
            return found;
        }
        // Some networks do not pass broadcasts on. Where it was last time is worth a try.
        String last = prefs.lastHost();
        return last.isEmpty() ? null : parse(last);
    }

    private static InetSocketAddress parse(String text) {
        try {
            String hostPart = text;
            int port = DEFAULT_PORT;
            int colon = text.lastIndexOf(':');
            if (colon > 0 && text.indexOf(':') == colon) {
                hostPart = text.substring(0, colon);
                port = Integer.parseInt(text.substring(colon + 1).trim());
            }
            return new InetSocketAddress(InetAddress.getByName(hostPart.trim()), port);
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    private InetSocketAddress discover() {
        try (DatagramSocket probe = new DatagramSocket()) {
            probe.setBroadcast(true);
            probe.setSoTimeout(700);
            for (InetAddress target : broadcastAddresses()) {
                try {
                    probe.send(new DatagramPacket(DISCOVERY_QUESTION, DISCOVERY_QUESTION.length, target, DEFAULT_PORT));
                } catch (IOException ignored) {
                    // an interface that is down
                }
            }
            byte[] buffer = new byte[512];
            DatagramPacket answer = new DatagramPacket(buffer, buffer.length);
            probe.receive(answer);
            JSONObject json = new JSONObject(new String(buffer, 0, answer.getLength(), StandardCharsets.UTF_8));
            if (!"neomixer".equals(json.optString("t"))) {
                return null;
            }
            return new InetSocketAddress(answer.getAddress(), json.optInt("port", DEFAULT_PORT));
        } catch (SocketTimeoutException e) {
            return null;
        } catch (IOException | JSONException | RuntimeException e) {
            Log.i(TAG, "Looking for the mixer failed: " + e);
            return null;
        }
    }

    private static List<InetAddress> broadcastAddresses() {
        List<InetAddress> addresses = new ArrayList<>();
        try {
            for (NetworkInterface network : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!network.isUp() || network.isLoopback()) {
                    continue;
                }
                for (InterfaceAddress address : network.getInterfaceAddresses()) {
                    if (address.getBroadcast() != null) {
                        addresses.add(address.getBroadcast());
                    }
                }
            }
            addresses.add(InetAddress.getByName("255.255.255.255"));
        } catch (IOException | RuntimeException ignored) {
            // then there is nowhere to ask
        }
        return addresses;
    }

    private void closeSocket() {
        Socket connection = socket;
        socket = null;
        out = null;
        if (connection != null) {
            try {
                connection.close();
            } catch (IOException ignored) {
                // closing is all that was wanted
            }
        }
    }

    private void pause(long millis) {
        synchronized (wake) {
            if (running() && !retry) {
                try {
                    wake.wait(millis);
                } catch (InterruptedException ignored) {
                    // the loop looks at "running" next
                }
            }
        }
    }

    private void setStatus(Status newStatus, String newHost) {
        main.post(() -> {
            status = newStatus;
            host = newHost;
            if (newStatus != Status.CONNECTED) {
                state = null;
            }
            notifyChanged();
        });
    }

    private void notifyChanged() {
        for (Listener listener : listeners) {
            listener.onLinkChanged();
        }
    }
}
