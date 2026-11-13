package org.neomixer.remote;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.provider.Settings;

import java.security.SecureRandom;

/** What the app keeps between runs. */
final class Prefs {
    static final int LATENCY_LOW = 0;
    static final int LATENCY_NORMAL = 1;
    static final int LATENCY_SAFE = 2;

    private final SharedPreferences prefs;

    Prefs(Context context) {
        prefs = context.getApplicationContext().getSharedPreferences("neomixer", Context.MODE_PRIVATE);
    }

    /** Who this phone is to a mixer. Made once, a mixer knows it by this from then on. */
    String phoneId() {
        return random("id", 8);
    }

    /** What this phone proves itself with. Only sent when pairing. */
    String secret() {
        return random("secret", 32);
    }

    private synchronized String random(String key, int bytes) {
        String value = prefs.getString(key, null);
        if (value == null) {
            byte[] raw = new byte[bytes];
            new SecureRandom().nextBytes(raw);
            value = hex(raw);
            prefs.edit().putString(key, value).apply();
        }
        return value;
    }

    static String hex(byte[] raw) {
        StringBuilder text = new StringBuilder(raw.length * 2);
        for (byte b : raw) {
            text.append(Character.forDigit((b >> 4) & 0xf, 16)).append(Character.forDigit(b & 0xf, 16));
        }
        return text.toString();
    }

    static byte[] unhex(String text) {
        byte[] raw = new byte[text.length() / 2];
        for (int i = 0; i < raw.length; i++) {
            raw[i] = (byte) Integer.parseInt(text.substring(i * 2, i * 2 + 2), 16);
        }
        return raw;
    }

    static String phoneName(Context context) {
        String name = null;
        try {
            name = Settings.Global.getString(context.getContentResolver(), Settings.Global.DEVICE_NAME);
        } catch (RuntimeException ignored) {
            // some devices do not let apps read it
        }
        return name == null || name.trim().isEmpty() ? Build.MODEL : name.trim();
    }

    /** The address typed in by hand, empty to look for the mixer. */
    String host() {
        return prefs.getString("host", "");
    }

    void setHost(String host) {
        prefs.edit().putString("host", host.trim()).apply();
    }

    /** Where the mixer was last time, tried when looking for it finds nothing. */
    String lastHost() {
        return prefs.getString("last_host", "");
    }

    void setLastHost(String host) {
        prefs.edit().putString("last_host", host).apply();
    }

    boolean listen() {
        return prefs.getBoolean("listen", true);
    }

    void setListen(boolean on) {
        prefs.edit().putBoolean("listen", on).apply();
    }

    boolean mic() {
        return prefs.getBoolean("mic", true);
    }

    void setMic(boolean on) {
        prefs.edit().putBoolean("mic", on).apply();
    }

    int latency() {
        return prefs.getInt("latency", LATENCY_NORMAL);
    }

    void setLatency(int latency) {
        prefs.edit().putInt("latency", latency).apply();
    }

    boolean showOffline() {
        return prefs.getBoolean("show_offline", false);
    }

    void setShowOffline(boolean on) {
        prefs.edit().putBoolean("show_offline", on).apply();
    }

    int tab() {
        return prefs.getInt("tab", 0);
    }

    void setTab(int tab) {
        prefs.edit().putInt("tab", tab).apply();
    }
}
