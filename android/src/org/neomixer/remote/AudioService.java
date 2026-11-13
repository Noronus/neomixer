package org.neomixer.remote;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import java.net.InetAddress;

/**
 * Keeps the audio going while the app is not on screen: owns the engine, the connection it needs
 * and the notification Android wants for it.
 */
public final class AudioService extends Service implements Link.Listener {
    private static final String TAG = "NeoMixerAudio";
    private static final String CHANNEL = "audio";
    private static final int NOTIFICATION = 1;
    static final String ACTION_STOP = "org.neomixer.remote.STOP";
    static final String ACTION_UPDATE = "org.neomixer.remote.UPDATE";

    /** The running service, for the screen to read levels from. Main thread only. */
    static AudioService instance;

    AudioEngine engine;
    private Link link;
    private Prefs prefs;
    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;
    private boolean started;

    static void start(Context context) {
        context.startForegroundService(new Intent(context, AudioService.class));
    }

    static void update(Context context) {
        if (instance != null) {
            context.startService(new Intent(context, AudioService.class).setAction(ACTION_UPDATE));
        }
    }

    static void stop(Context context) {
        if (instance != null) {
            context.startService(new Intent(context, AudioService.class).setAction(ACTION_STOP));
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        prefs = new Prefs(this);
        link = Link.get(this);
        NotificationChannel channel = new NotificationChannel(CHANNEL, getString(R.string.notif_channel), NotificationManager.IMPORTANCE_LOW);
        channel.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf();
            return START_NOT_STICKY;
        }

        boolean mic = prefs.mic() && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        try {
            showNotification(mic);
        } catch (RuntimeException e) {
            // Android refuses a microphone service started from the background
            Log.e(TAG, "Could not go to the foreground", e);
            stopSelf();
            return START_NOT_STICKY;
        }

        if (!started) {
            started = true;
            try {
                engine = new AudioEngine(prefs.latency());
                int port = engine.start(prefs.listen());
                holdLocks();
                instance = this;
                link.addListener(this);
                link.acquire(this);
                link.setAudio(port);
            } catch (java.io.IOException | RuntimeException e) {
                Log.e(TAG, "Could not start the audio", e);
                stopSelf();
                return START_NOT_STICKY;
            }
        }

        engine.setListen(prefs.listen());
        engine.setLatency(prefs.latency());
        applyMic(mic);
        return START_NOT_STICKY;
    }

    private void applyMic(boolean wanted) {
        if (wanted && link.audioOn && link.micPort > 0) {
            try {
                engine.setMicTarget(InetAddress.getByName(link.host), link.micPort);
                engine.setMic(true);
                return;
            } catch (java.io.IOException | RuntimeException e) {
                Log.e(TAG, "No address for the microphone", e);
            }
        }
        engine.setMic(false);
    }

    @Override
    public void onLinkChanged() {
        if (engine == null) {
            return;
        }
        // Another phone took the audio: this one must not go on as if it still had it
        if ("taken".equals(link.audioError)) {
            stopSelf();
            return;
        }
        boolean mic = prefs.mic() && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        applyMic(mic);
    }

    @Override
    public void onState(MixerState state) {
        // the screen's business
    }

    @Override
    public void onDestroy() {
        if (instance == this) {
            instance = null;
        }
        if (started) {
            link.removeListener(this);
            link.setAudio(0);
            // Not on this thread: the last message still has to go out before the connection may close
            AudioEngine stopping = engine;
            Link held = link;
            new Thread(() -> {
                if (stopping != null) {
                    stopping.stop();
                }
                try {
                    Thread.sleep(300);
                } catch (InterruptedException ignored) {
                    // then it closes a little early
                }
                held.release(AudioService.this);
            }, "neomixer-stop").start();
        }
        engine = null;
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
        super.onDestroy();
    }

    @SuppressWarnings("deprecation")  // the only mode there is before Android 10
    private void holdLocks() {
        PowerManager power = getSystemService(PowerManager.class);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "neomixer:audio");
        wakeLock.acquire();
        WifiManager wifi = getApplicationContext().getSystemService(WifiManager.class);
        if (wifi != null) {
            // Without it the radio dozes between packets, which is heard as dropouts
            int mode = Build.VERSION.SDK_INT >= 29 ? WifiManager.WIFI_MODE_FULL_LOW_LATENCY : WifiManager.WIFI_MODE_FULL_HIGH_PERF;
            wifiLock = wifi.createWifiLock(mode, "neomixer:audio");
            wifiLock.acquire();
        }
    }

    private void showNotification(boolean mic) {
        int immutable = PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT;
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), immutable);
        PendingIntent stop = PendingIntent.getService(
                this, 1, new Intent(this, AudioService.class).setAction(ACTION_STOP), immutable);

        StringBuilder text = new StringBuilder();
        if (prefs.listen()) {
            text.append(getString(R.string.notif_listen));
        }
        if (mic) {
            text.append(text.length() > 0 ? " · " : "").append(getString(R.string.notif_mic));
        }

        Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle(getString(R.string.notif_title))
                .setContentText(text)
                .setContentIntent(open)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(new Notification.Action.Builder(null, getString(R.string.notif_stop), stop).build())
                .build();

        if (Build.VERSION.SDK_INT >= 30) {
            int types = ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK;
            if (mic) {
                types |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
            }
            startForeground(NOTIFICATION, notification, types);
        } else {
            startForeground(NOTIFICATION, notification);
        }
    }
}
