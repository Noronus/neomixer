package org.neomixer.remote;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** What the mixer last said it looks like. Never changed after it was parsed. */
final class MixerState {

    static final class Strip {
        String type;
        String id;
        String name;
        int volume;
        boolean mute;
        boolean present;
        /** One of the two devices that are a phone's audio */
        boolean phone;
        /** A1, B2: outputs only */
        String tag = "";
        /** The outputs an input plays into, as type + id */
        final Set<String> routes = new HashSet<>();

        boolean isInput() {
            return type.equals("hi") || type.equals("vi");
        }

        String key() {
            return type + id;
        }
    }

    static final class App {
        String appType;
        String key;
        String name;
        int volume;
        boolean mute;
        /** active, idle or offline */
        String state;

        String key() {
            return appType + "/" + key;
        }
    }

    final List<Strip> strips = new ArrayList<>();
    final List<App> apps = new ArrayList<>();

    List<Strip> outputs() {
        List<Strip> outputs = new ArrayList<>();
        for (Strip strip : strips) {
            if (!strip.isInput()) {
                outputs.add(strip);
            }
        }
        return outputs;
    }

    static MixerState parse(JSONObject message) throws JSONException {
        MixerState state = new MixerState();
        JSONArray strips = message.getJSONArray("strips");
        for (int i = 0; i < strips.length(); i++) {
            JSONObject json = strips.getJSONObject(i);
            Strip strip = new Strip();
            strip.type = json.getString("type");
            strip.id = json.getString("id");
            strip.name = json.optString("name", "?");
            strip.volume = json.optInt("volume", 100);
            strip.mute = json.optBoolean("mute");
            strip.present = json.optBoolean("present", true);
            strip.phone = json.optBoolean("phone");
            strip.tag = json.optString("tag", "");
            JSONArray routes = json.optJSONArray("routes");
            for (int r = 0; routes != null && r < routes.length(); r++) {
                strip.routes.add(routes.getString(r));
            }
            state.strips.add(strip);
        }

        JSONArray apps = message.optJSONArray("apps");
        for (int i = 0; apps != null && i < apps.length(); i++) {
            JSONObject json = apps.getJSONObject(i);
            App app = new App();
            app.appType = json.getString("app_type");
            app.key = json.getString("key");
            app.name = json.optString("name", app.key);
            app.volume = json.optInt("volume", 100);
            app.mute = json.optBoolean("mute");
            app.state = json.optString("state", "offline");
            state.apps.add(app);
        }
        return state;
    }
}
