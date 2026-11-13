package org.neomixer.remote;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.PorterDuff;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The one screen: the mixer's strips, the tabs they are sorted into at the bottom, and the audio
 * button in the corner above those.
 */
public final class MainActivity extends Activity implements Link.Listener {
    private static final int REQUEST_PERMISSIONS = 1;
    private static final int REQUEST_LOCAL_NETWORK = 2;
    /** Android 16 and newer only let an app talk to the local network once the user allowed it. */
    private static final String LOCAL_NETWORK = "android.permission.ACCESS_LOCAL_NETWORK";
    private static final int TAB_INPUTS = 0;
    private static final int TAB_OUTPUTS = 1;
    private static final int TAB_APPS = 2;
    private static final long LEVEL_TICK_MS = 70;
    /** For this long after a tap, what the mixer reports is still from before it. */
    private static final long SETTLE_MS = 600;

    private Link link;
    private Prefs prefs;
    private final Handler handler = new Handler(Looper.getMainLooper());

    private View statusDot;
    private TextView statusText;
    private RoundButton audioButton;
    private RoundButton listenButton;
    private RoundButton micButton;
    private TextView audioNote;
    private final TextView[] tabs = new TextView[3];
    private ScrollView scroll;
    private LinearLayout list;

    private int tab;
    private String shownSignature = "";
    private final Map<String, Row> rows = new HashMap<>();
    private boolean visible;

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        link = Link.get(this);
        prefs = new Prefs(this);
        tab = prefs.tab();
        setVolumeControlStream(AudioManager.STREAM_MUSIC);
        setContentView(buildScreen());
    }

    @Override
    protected void onStart() {
        super.onStart();
        visible = true;
        link.addListener(this);
        link.acquire(this);
        if (localNetworkMissing() && !askedForLocalNetwork) {
            askedForLocalNetwork = true;
            requestPermissions(new String[] {LOCAL_NETWORK}, REQUEST_LOCAL_NETWORK);
        }
        render();
        handler.post(levelTick);
    }

    private boolean askedForLocalNetwork;

    private boolean localNetworkMissing() {
        return Build.VERSION.SDK_INT >= 36 && checkSelfPermission(LOCAL_NETWORK) != PackageManager.PERMISSION_GRANTED;
    }

    @Override
    protected void onStop() {
        visible = false;
        handler.removeCallbacks(levelTick);
        link.removeListener(this);
        link.release(this);
        super.onStop();
    }

    @Override
    public void onLinkChanged() {
        render();
    }

    @Override
    public void onState(MixerState state) {
        render();
    }

    //
    // Building the screen
    //
    private View buildScreen() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Ui.BG);

        root.addView(buildHeader(), matchWrap());

        audioNote = Ui.text(this, 12.5f, Ui.B, false);
        audioNote.setBackground(Ui.outlined(this, Ui.alpha(Ui.B, 0.1f), Ui.alpha(Ui.B, 0.5f), 10));
        audioNote.setPadding(dp(12), dp(8), dp(12), dp(8));
        audioNote.setVisibility(View.GONE);
        root.addView(audioNote, margins(matchWrap(), 12, 0, 12, 8));

        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        // Room at the end, so the last strip can be scrolled out from under the audio button
        list.setPadding(dp(12), dp(6), dp(12), dp(104));
        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setClipToPadding(false);
        scroll.addView(list, new ScrollView.LayoutParams(ScrollView.LayoutParams.MATCH_PARENT, ScrollView.LayoutParams.WRAP_CONTENT));
        root.addView(scroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));
        // At the bottom, where a thumb reaches them
        root.addView(buildTabs(), margins(matchWrap(), 12, 6, 12, 8));

        FrameLayout screen = new FrameLayout(this);
        screen.setBackgroundColor(Ui.BG);
        screen.setOnApplyWindowInsetsListener(MainActivity::keepClearOfBars);
        screen.addView(root, new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        FrameLayout.LayoutParams corner = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM | Gravity.END);
        // above the tabs
        corner.setMargins(0, 0, dp(18), dp(78));
        screen.addView(buildAudioButtons(), corner);
        return screen;
    }

    /** The screen is drawn edge to edge, so the system's bars and the keyboard have to be left room. */
    @SuppressWarnings("deprecation")  // the only way there is before Android 11
    private static WindowInsets keepClearOfBars(View view, WindowInsets insets) {
        if (Build.VERSION.SDK_INT >= 30) {
            android.graphics.Insets bars = insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
        } else {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
        }
        return insets;
    }

    private View buildHeader() {
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(16), dp(12), dp(8), dp(8));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);

        LinearLayout brand = new LinearLayout(this);
        TextView neo = Ui.text(this, 22, Ui.VI, true);
        neo.setText("Neo");
        TextView mixer = Ui.text(this, 22, Ui.TEXT, true);
        mixer.setText("Mixer");
        brand.addView(neo);
        brand.addView(mixer);
        titles.addView(brand);

        LinearLayout status = new LinearLayout(this);
        status.setGravity(Gravity.CENTER_VERTICAL);
        statusDot = new View(this);
        status.addView(statusDot, margins(new LinearLayout.LayoutParams(dp(8), dp(8)), 0, 0, 7, 0));
        statusText = Ui.text(this, 13, Ui.DIM, false);
        statusText.setMaxLines(2);
        status.addView(statusText);
        titles.addView(status, margins(matchWrap(), 0, 2, 0, 0));
        header.addView(titles, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView settings = Ui.text(this, 22, Ui.DIM, true);
        settings.setText("⋮");
        settings.setGravity(Gravity.CENTER);
        settings.setContentDescription(getString(R.string.settings));
        settings.setOnClickListener(view -> showSettings());
        header.addView(settings, new LinearLayout.LayoutParams(dp(48), dp(48)));
        return header;
    }

    /** The audio button, and above it one for each direction while the audio runs. */
    private View buildAudioButtons() {
        LinearLayout column = new LinearLayout(this);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setGravity(Gravity.CENTER_HORIZONTAL);

        micButton = new RoundButton(this, R.drawable.ic_mic, 46, Ui.HI);
        micButton.setContentDescription(getString(R.string.mic));
        micButton.setOnClickListener(view -> onDirection(false));
        column.addView(micButton, margins(new LinearLayout.LayoutParams(dp(46), dp(46)), 0, 0, 0, 10));

        listenButton = new RoundButton(this, R.drawable.ic_speaker, 46, Ui.A);
        listenButton.setContentDescription(getString(R.string.listen));
        listenButton.setOnClickListener(view -> onDirection(true));
        column.addView(listenButton, margins(new LinearLayout.LayoutParams(dp(46), dp(46)), 0, 0, 0, 12));

        audioButton = new RoundButton(this, R.drawable.ic_headset, 64, Ui.VI);
        audioButton.setElevation(dp(6));
        audioButton.setOnClickListener(view -> onAudioButton());
        column.addView(audioButton, new LinearLayout.LayoutParams(dp(64), dp(64)));
        return column;
    }

    private View buildTabs() {
        LinearLayout bar = new LinearLayout(this);
        bar.setBackground(Ui.rounded(this, Ui.PANEL, 12));
        bar.setPadding(dp(4), dp(4), dp(4), dp(4));
        int[] labels = {R.string.tab_inputs, R.string.tab_outputs, R.string.tab_apps};
        for (int i = 0; i < tabs.length; i++) {
            int index = i;
            TextView label = Ui.text(this, 14, Ui.DIM, true);
            label.setText(labels[i]);
            label.setGravity(Gravity.CENTER);
            label.setPadding(0, dp(9), 0, dp(9));
            label.setOnClickListener(view -> {
                tab = index;
                prefs.setTab(index);
                scroll.scrollTo(0, 0);
                render();
            });
            tabs[i] = label;
            bar.addView(label, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        }
        return bar;
    }

    //
    // Showing what is
    //
    private void render() {
        if (!visible) {
            return;
        }
        renderStatus();
        renderAudio();
        renderTabs();
        renderList();
    }

    private void renderStatus() {
        int color = Ui.B;
        String text;
        switch (link.status) {
            case CONNECTED:
                color = Ui.A;
                text = link.mixerName.isEmpty() ? link.host : link.mixerName;
                break;
            case CONNECTING:
                text = getString(R.string.status_connecting, link.host);
                break;
            case PAIRING:
                color = Ui.VI;
                text = getString(R.string.status_pairing);
                break;
            case DENIED:
                color = Ui.MUTE;
                text = getString(R.string.status_denied);
                break;
            default:
                text = getString(R.string.status_searching);
                break;
        }
        statusText.setText(text);
        statusDot.setBackground(Ui.rounded(this, color, 4));
    }

    private boolean audioRunning() {
        return AudioService.instance != null;
    }

    private void renderAudio() {
        boolean running = audioRunning();
        boolean on = running && link.audioOn;
        boolean connected = link.status == Link.Status.CONNECTED;

        // Violet to start it, green while it plays, and in between while the PC sets it up
        audioButton.setIcon(running ? R.drawable.ic_stop : R.drawable.ic_headset);
        audioButton.setLook(on ? Ui.A : Ui.VI, true, on ? 0xff06281d : 0xff17122b);
        audioButton.setAlpha(connected || running ? (running && !on ? 0.7f : 1f) : 0.4f);
        audioButton.setContentDescription(getString(on ? R.string.audio_on : running ? R.string.audio_starting : R.string.audio_start));

        listenButton.setVisibility(running ? View.VISIBLE : View.GONE);
        micButton.setVisibility(running ? View.VISIBLE : View.GONE);
        listenButton.setLook(Ui.A, prefs.listen(), 0xff06281d);
        micButton.setLook(Ui.HI, prefs.mic(), 0xff06202b);

        String note = null;
        if (link.audioError != null && !link.audioError.isEmpty()) {
            note = "taken".equals(link.audioError)
                    ? getString(R.string.audio_taken) : getString(R.string.audio_failed, link.audioError);
        } else if (on && prefs.listen() && nothingRoutedToPhone()) {
            note = getString(R.string.route_hint);
        }
        audioNote.setText(note);
        audioNote.setVisibility(note == null ? View.GONE : View.VISIBLE);
    }

    private boolean nothingRoutedToPhone() {
        MixerState state = link.state;
        if (state == null) {
            return false;
        }
        for (MixerState.Strip output : state.outputs()) {
            if (!output.phone) {
                continue;
            }
            for (MixerState.Strip strip : state.strips) {
                if (strip.routes.contains(output.key())) {
                    return false;
                }
            }
            return true;
        }
        return false;
    }

    private void renderTabs() {
        int[] colors = {Ui.VI, Ui.A, Ui.APP};
        for (int i = 0; i < tabs.length; i++) {
            boolean selected = i == tab;
            tabs[i].setTextColor(selected ? colors[i] : Ui.DIM);
            tabs[i].setBackground(selected ? Ui.rounded(this, Ui.STRIP_HIGH, 9) : null);
        }
    }

    /** One line of the list: a section title, a strip, a program, or a note. */
    private static final class Item {
        String key;
        String title;
        int color;
        MixerState.Strip strip;
        MixerState.App app;
    }

    private List<Item> items(MixerState state) {
        List<Item> items = new ArrayList<>();
        if (tab == TAB_APPS) {
            boolean offline = prefs.showOffline();
            addApps(items, state, "sink_input", R.string.section_playing, offline);
            addApps(items, state, "source_output", R.string.section_recording, offline);
            return items;
        }

        String[] types = tab == TAB_INPUTS ? new String[] {"hi", "vi"} : new String[] {"a", "b"};
        int[] titles = tab == TAB_INPUTS
                ? new int[] {R.string.section_hi, R.string.section_vi} : new int[] {R.string.section_a, R.string.section_b};
        for (int i = 0; i < types.length; i++) {
            boolean first = true;
            for (MixerState.Strip strip : state.strips) {
                if (!strip.type.equals(types[i])) {
                    continue;
                }
                if (first) {
                    items.add(section(getString(titles[i]), Ui.colorOf(types[i])));
                    first = false;
                }
                Item item = new Item();
                item.key = strip.key();
                item.strip = strip;
                items.add(item);
            }
        }
        return items;
    }

    private void addApps(List<Item> items, MixerState state, String appType, int title, boolean offline) {
        boolean first = true;
        // Whatever plays comes first, then what is only connected
        for (String wanted : new String[] {"active", "idle", "offline"}) {
            if (wanted.equals("offline") && !offline) {
                continue;
            }
            for (MixerState.App app : state.apps) {
                if (!app.appType.equals(appType) || !app.state.equals(wanted)) {
                    continue;
                }
                if (first) {
                    items.add(section(getString(title), Ui.APP));
                    first = false;
                }
                Item item = new Item();
                item.key = app.key();
                item.app = app;
                items.add(item);
            }
        }
    }

    private static Item section(String title, int color) {
        Item item = new Item();
        item.key = "#" + title;
        item.title = title;
        item.color = color;
        return item;
    }

    private void renderList() {
        MixerState state = link.status == Link.Status.CONNECTED ? link.state : null;
        if (state == null) {
            showMessage();
            return;
        }

        List<Item> items = items(state);
        List<MixerState.Strip> outputs = state.outputs();

        // The list is only built anew when what is in it changed, not for every move of a fader
        StringBuilder signature = new StringBuilder().append(tab).append(prefs.showOffline());
        for (Item item : items) {
            signature.append('|').append(item.key);
            if (item.strip != null) {
                signature.append(item.strip.name).append(item.strip.tag);
            } else if (item.app != null) {
                signature.append(item.app.name);
            }
        }
        if (tab == TAB_INPUTS) {
            for (MixerState.Strip output : outputs) {
                signature.append('>').append(output.key()).append(output.tag).append(output.name);
            }
        }

        if (!signature.toString().equals(shownSignature)) {
            shownSignature = signature.toString();
            list.removeAllViews();
            rows.clear();
            for (Item item : items) {
                if (item.title != null) {
                    TextView title = Ui.text(this, 11.5f, item.color, true);
                    title.setText(item.title.toUpperCase());
                    title.setLetterSpacing(0.1f);
                    list.addView(title, margins(matchWrap(), 4, 14, 0, 6));
                } else {
                    Row row = item.strip != null ? new StripRow(this, item.strip, outputs) : new AppRow(this, item.app);
                    rows.put(item.key, row);
                    list.addView(row, margins(matchWrap(), 0, 0, 0, 8));
                }
            }
            if (items.isEmpty()) {
                TextView empty = Ui.text(this, 14, Ui.DIM, false);
                empty.setText(tab == TAB_APPS ? R.string.empty_apps : R.string.empty_strips);
                empty.setGravity(Gravity.CENTER);
                list.addView(empty, margins(matchWrap(), 16, 40, 16, 16));
            }
            if (tab == TAB_APPS) {
                CheckBox offline = new CheckBox(this);
                offline.setText(R.string.show_offline);
                offline.setTextColor(Ui.DIM);
                offline.setChecked(prefs.showOffline());
                offline.setOnCheckedChangeListener((button, checked) -> {
                    prefs.setShowOffline(checked);
                    render();
                });
                list.addView(offline, margins(matchWrap(), 0, 8, 0, 0));
            }
        }

        for (Item item : items) {
            Row row = rows.get(item.key);
            if (row instanceof StripRow) {
                ((StripRow) row).show(item.strip);
            } else if (row instanceof AppRow) {
                ((AppRow) row).show(item.app);
            }
        }
    }

    private boolean askedTwice;

    private void showMessage() {
        boolean blocked = localNetworkMissing() && link.status != Link.Status.CONNECTED;
        String signature = "message" + link.status + blocked;
        if (signature.equals(shownSignature)) {
            return;
        }
        shownSignature = signature;
        list.removeAllViews();
        rows.clear();

        TextView hint = Ui.text(this, 14, Ui.DIM, false);
        hint.setGravity(Gravity.CENTER);
        hint.setLineSpacing(dp(3), 1f);
        switch (link.status) {
            case PAIRING:
                hint.setText(R.string.status_pairing);
                hint.setTextColor(Ui.TEXT);
                break;
            case DENIED:
                hint.setText(R.string.status_denied);
                break;
            case CONNECTED:
                hint.setText("");
                break;
            default:
                hint.setText(blocked ? R.string.local_network_needed : R.string.hint_searching);
                break;
        }
        list.addView(hint, margins(matchWrap(), 20, 48, 20, 16));

        if (blocked) {
            TextView allow = Ui.text(this, 15, Ui.TEXT, true);
            allow.setText(R.string.local_network_allow);
            allow.setGravity(Gravity.CENTER);
            allow.setPadding(dp(20), dp(12), dp(20), dp(12));
            allow.setBackground(Ui.outlined(this, Ui.alpha(Ui.VI, 0.18f), Ui.VI, 10));
            allow.setOnClickListener(view -> {
                // Android stops asking after two refusals: then only its settings can allow it
                if (askedTwice && !shouldShowRequestPermissionRationale(LOCAL_NETWORK)) {
                    startActivity(new android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            android.net.Uri.fromParts("package", getPackageName(), null)));
                } else {
                    askedTwice = true;
                    requestPermissions(new String[] {LOCAL_NETWORK}, REQUEST_LOCAL_NETWORK);
                }
            });
            LinearLayout.LayoutParams params = wrapWrap();
            params.gravity = Gravity.CENTER_HORIZONTAL;
            list.addView(allow, params);
        }

        if (link.status == Link.Status.DENIED) {
            TextView retry = Ui.text(this, 15, Ui.TEXT, true);
            retry.setText(R.string.retry);
            retry.setGravity(Gravity.CENTER);
            retry.setPadding(dp(20), dp(12), dp(20), dp(12));
            retry.setBackground(Ui.outlined(this, Ui.alpha(Ui.VI, 0.18f), Ui.VI, 10));
            retry.setOnClickListener(view -> link.reconnect());
            LinearLayout.LayoutParams params = wrapWrap();
            params.gravity = Gravity.CENTER_HORIZONTAL;
            list.addView(retry, params);
        }
    }

    //
    // Rows
    //
    /** What a strip and a program have in common: a name, a fader and a mute button. */
    private abstract class Row extends LinearLayout {
        final TextView name;
        final TextView note;
        final TextView db;
        final FaderView fader;
        final TextView mute;
        private boolean muted;
        private long muteTapped;

        Row(Context context, int color) {
            super(context);
            setOrientation(VERTICAL);
            setBackground(Ui.outlined(context, Ui.STRIP, Ui.LINE, 14));
            setPadding(dp(12), dp(10), dp(10), dp(10));

            LinearLayout top = new LinearLayout(context);
            top.setGravity(Gravity.CENTER_VERTICAL);
            View pill = new View(context);
            pill.setBackground(Ui.rounded(context, color, 2));
            top.addView(pill, margins(new LayoutParams(dp(4), dp(18)), 0, 0, 8, 0));
            name = Ui.text(context, 16, Ui.TEXT, true);
            name.setSingleLine(true);
            name.setEllipsize(TextUtils.TruncateAt.END);
            top.addView(name, new LayoutParams(0, LayoutParams.WRAP_CONTENT, 1));
            note = Ui.text(context, 12, Ui.DIM, false);
            top.addView(note, margins(wrapWrap(), 8, 0, 0, 0));
            db = Ui.text(context, 13, Ui.DIM, false);
            db.setTypeface(Typeface.MONOSPACE);
            db.setGravity(Gravity.END);
            top.addView(db, margins(new LayoutParams(dp(76), LayoutParams.WRAP_CONTENT), 8, 0, 2, 0));
            addView(top, matchWrap());

            LinearLayout controls = new LinearLayout(context);
            controls.setGravity(Gravity.CENTER_VERTICAL);
            fader = new FaderView(context);
            fader.setColor(color);
            fader.setListener(value -> {
                db.setText(Ui.db(value));
                sendVolume(value);
            });
            controls.addView(fader, new LayoutParams(0, dp(46), 1));
            mute = Ui.text(context, 15, Ui.DIM, true);
            mute.setText(R.string.mute);
            mute.setGravity(Gravity.CENTER);
            mute.setOnClickListener(view -> {
                muteTapped = SystemClock.uptimeMillis();
                setMuted(!muted);
                sendMute(muted);
            });
            controls.addView(mute, margins(new LayoutParams(dp(46), dp(40)), 8, 0, 0, 0));
            addView(controls, margins(matchWrap(), 0, 4, 0, 0));
            setMuted(false);
        }

        abstract void sendVolume(int volume);

        abstract void sendMute(boolean muted);

        void showLevels(int volume, boolean isMuted) {
            fader.showValue(volume);
            db.setText(Ui.db(fader.getValue()));
            if (SystemClock.uptimeMillis() - muteTapped > SETTLE_MS && isMuted != muted) {
                setMuted(isMuted);
            }
        }

        private void setMuted(boolean isMuted) {
            muted = isMuted;
            mute.setTextColor(isMuted ? 0xffffffff : Ui.DIM);
            mute.setBackground(isMuted
                    ? Ui.rounded(getContext(), Ui.MUTE, 10) : Ui.outlined(getContext(), Ui.STRIP_HIGH, Ui.LINE, 10));
            fader.setDimmed(isMuted);
        }
    }

    private final class StripRow extends Row {
        private MixerState.Strip strip;
        private final Map<String, RouteChip> chips = new HashMap<>();

        StripRow(Context context, MixerState.Strip strip, List<MixerState.Strip> outputs) {
            super(context, Ui.colorOf(strip.type));
            this.strip = strip;
            if (!strip.isInput() || outputs.isEmpty()) {
                return;
            }
            Ui.FlowLayout routes = new Ui.FlowLayout(context, 6);
            for (MixerState.Strip output : outputs) {
                RouteChip chip = new RouteChip(context, output);
                chip.setOnClickListener(view -> {
                    chip.tapped = SystemClock.uptimeMillis();
                    chip.setOn(!chip.on);
                    link.setRoute(this.strip, output, chip.on);
                });
                chips.put(output.key(), chip);
                routes.addView(chip);
            }
            addView(routes, margins(matchWrap(), 0, 8, 0, 0));
        }

        void show(MixerState.Strip current) {
            strip = current;
            name.setText(current.tag.isEmpty() ? current.name : current.tag + "  " + current.name);
            name.setTextColor(current.present ? Ui.TEXT : Ui.FAINT);
            note.setText(current.present ? "" : getString(R.string.absent));
            showLevels(current.volume, current.mute);
            long now = SystemClock.uptimeMillis();
            for (Map.Entry<String, RouteChip> entry : chips.entrySet()) {
                RouteChip chip = entry.getValue();
                boolean on = current.routes.contains(entry.getKey());
                if (now - chip.tapped > SETTLE_MS && on != chip.on) {
                    chip.setOn(on);
                }
            }
        }

        @Override
        void sendVolume(int volume) {
            link.setVolume(strip, volume);
        }

        @Override
        void sendMute(boolean muted) {
            link.setMute(strip, muted);
        }
    }

    private final class AppRow extends Row {
        private MixerState.App app;

        AppRow(Context context, MixerState.App app) {
            super(context, Ui.APP);
            this.app = app;
        }

        void show(MixerState.App current) {
            app = current;
            boolean offline = current.state.equals("offline");
            name.setText(current.name);
            name.setTextColor(offline ? Ui.FAINT : Ui.TEXT);
            note.setText(current.state.equals("active") ? ""
                    : getString(offline ? R.string.app_offline : R.string.app_idle));
            showLevels(current.volume, current.mute);
        }

        @Override
        void sendVolume(int volume) {
            link.setAppVolume(app, volume);
        }

        @Override
        void sendMute(boolean muted) {
            link.setAppMute(app, muted);
        }
    }

    /** A route of an input: the output's tag and name, lit while it plays there. */
    private final class RouteChip extends TextView {
        private final int color;
        boolean on;
        long tapped;

        RouteChip(Context context, MixerState.Strip output) {
            super(context);
            color = Ui.colorOf(output.type);
            setText(output.tag + "  " + output.name);
            setTextSize(13);
            setTypeface(Typeface.DEFAULT_BOLD);
            setSingleLine(true);
            setEllipsize(TextUtils.TruncateAt.END);
            setMaxWidth(dp(170));
            setMinHeight(dp(38));
            setGravity(Gravity.CENTER);
            setPadding(dp(12), dp(7), dp(12), dp(7));
            setOn(false);
        }

        void setOn(boolean isOn) {
            on = isOn;
            setTextColor(isOn ? 0xff0b1220 : Ui.DIM);
            setBackground(isOn ? Ui.rounded(getContext(), color, 9) : Ui.outlined(getContext(), Ui.STRIP_HIGH, Ui.LINE, 9));
        }
    }

    /**
     * A round button with an icon: filled in its color when on, an outline when off. A ring around
     * it shows a level, which is how the two directions show that something is coming through.
     */
    private static final class RoundButton extends ImageView {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float ring;
        private int color;
        private float level;

        RoundButton(Context context, int icon, int sizeDp, int color) {
            super(context);
            this.color = color;
            ring = Ui.dp(context, 3);
            int padding = Ui.dp(context, sizeDp * 0.28f);
            setPadding(padding, padding, padding, padding);
            setImageResource(icon);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(ring);
            paint.setStrokeCap(Paint.Cap.ROUND);
            setLook(color, true, 0xff101010);
        }

        void setIcon(int icon) {
            setImageResource(icon);
        }

        void setLook(int newColor, boolean on, int iconOnColor) {
            color = newColor;
            GradientDrawable shape = new GradientDrawable();
            shape.setShape(GradientDrawable.OVAL);
            shape.setColor(on ? newColor : Ui.STRIP_HIGH);
            shape.setStroke(Math.max(1, Ui.dp(getContext(), 1)), on ? newColor : Ui.LINE);
            setBackground(shape);
            setColorFilter(on ? iconOnColor : Ui.FAINT, PorterDuff.Mode.SRC_IN);
        }

        void setLevel(float value) {
            // On a dB scale, 60 dB wide. Falls slower than it rises, so a peak can be seen.
            float scaled = value <= 0.001f ? 0 : Math.max(0, 1 + (float) (20 * Math.log10(value)) / 60f);
            float next = Math.max(scaled, level * 0.85f);
            if (next < 0.005f) {
                next = 0;
            }
            if (next != level) {
                level = next;
                invalidate();
            }
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            if (level > 0) {
                paint.setColor(0xffffffff);
                float inset = ring * 1.5f;
                canvas.drawArc(inset, inset, getWidth() - inset, getHeight() - inset, -90, 360 * level, false, paint);
            }
        }
    }

    private final Runnable levelTick = new Runnable() {
        private boolean wasRunning;

        @Override
        public void run() {
            AudioService service = AudioService.instance;
            AudioEngine engine = service == null ? null : service.engine;
            listenButton.setLevel(engine == null ? 0 : engine.playLevel);
            micButton.setLevel(engine == null ? 0 : engine.micLevel);
            // The service starts and stops without telling anyone
            if ((service != null) != wasRunning) {
                wasRunning = service != null;
                renderAudio();
            }
            if (engine != null && engine.micFailed && prefs.mic()) {
                prefs.setMic(false);
                AudioService.update(MainActivity.this);
                renderAudio();
            }
            if (visible) {
                handler.postDelayed(this, LEVEL_TICK_MS);
            }
        }
    };

    //
    // The audio button
    //
    private void onAudioButton() {
        if (audioRunning()) {
            AudioService.stop(this);
            return;
        }
        if (link.status != Link.Status.CONNECTED) {
            return;
        }
        if (!prefs.listen() && !prefs.mic()) {
            prefs.setListen(true);
        }
        if (askForPermissions()) {
            return;
        }
        startAudio();
    }

    private void onDirection(boolean listen) {
        if (listen) {
            prefs.setListen(!prefs.listen());
        } else {
            prefs.setMic(!prefs.mic());
        }
        renderAudio();
        if (!audioRunning()) {
            return;
        }
        if (!prefs.listen() && !prefs.mic()) {
            AudioService.stop(this);
            return;
        }
        if (!askForPermissions()) {
            AudioService.update(this);
        }
    }

    /** Ask for what the audio needs and is not allowed yet, returns if a question is now open. */
    private boolean askForPermissions() {
        List<String> missing = new ArrayList<>();
        if (prefs.mic() && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            missing.add(Manifest.permission.RECORD_AUDIO);
        }
        if (Build.VERSION.SDK_INT >= 33 && !audioRunning()
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && shouldAskForNotifications) {
            missing.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (missing.isEmpty()) {
            return false;
        }
        requestPermissions(missing.toArray(new String[0]), REQUEST_PERMISSIONS);
        return true;
    }

    /** Asked once per run: the audio works without its notification, so a no is not asked about again. */
    private boolean shouldAskForNotifications = true;

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        if (request == REQUEST_LOCAL_NETWORK) {
            // What was tried so far failed without it
            link.reconnect();
            shownSignature = "";
            render();
            return;
        }
        if (request != REQUEST_PERMISSIONS) {
            return;
        }
        shouldAskForNotifications = false;
        if (prefs.mic() && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            prefs.setMic(false);
            Toast.makeText(this, R.string.mic_denied, Toast.LENGTH_LONG).show();
            if (!prefs.listen() && !audioRunning()) {
                renderAudio();
                return;
            }
        }
        if (audioRunning()) {
            AudioService.update(this);
        } else if (link.status == Link.Status.CONNECTED) {
            startAudio();
        }
        renderAudio();
    }

    private void startAudio() {
        if (!prefs.listen() && !prefs.mic()) {
            return;
        }
        link.audioError = null;
        try {
            AudioService.start(this);
        } catch (RuntimeException e) {
            Toast.makeText(this, String.valueOf(e.getMessage()), Toast.LENGTH_LONG).show();
        }
        renderAudio();
    }

    //
    // Settings
    //
    private void showSettings() {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(22), dp(8), dp(22), 0);

        TextView hostLabel = Ui.text(this, 13, Ui.DIM, false);
        hostLabel.setText(R.string.settings_host);
        content.addView(hostLabel);
        EditText host = new EditText(this);
        host.setSingleLine(true);
        host.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        host.setHint(R.string.settings_host_hint);
        host.setText(prefs.host());
        content.addView(host, matchWrap());

        TextView latencyLabel = Ui.text(this, 13, Ui.DIM, false);
        latencyLabel.setText(R.string.settings_latency);
        content.addView(latencyLabel, margins(matchWrap(), 0, 14, 0, 0));
        RadioGroup latency = new RadioGroup(this);
        int[] labels = {R.string.latency_low, R.string.latency_normal, R.string.latency_safe};
        for (int i = 0; i < labels.length; i++) {
            RadioButton option = new RadioButton(this);
            option.setId(i + 1);
            option.setText(labels[i]);
            latency.addView(option);
        }
        latency.check(prefs.latency() + 1);
        content.addView(latency);

        new AlertDialog.Builder(this)
                .setTitle(R.string.settings)
                .setView(content)
                .setNegativeButton(R.string.cancel, null)
                .setPositiveButton(R.string.ok, (dialog, which) -> {
                    String typed = host.getText().toString().trim();
                    boolean moved = !typed.equals(prefs.host());
                    prefs.setHost(typed);
                    prefs.setLatency(Math.max(0, latency.getCheckedRadioButtonId() - 1));
                    if (moved) {
                        link.reconnect();
                    }
                    AudioService.update(this);
                })
                .show();
    }

    //
    // Layout shorthands
    //
    private int dp(float value) {
        return Ui.dp(this, value);
    }

    private static LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private static LinearLayout.LayoutParams wrapWrap() {
        return new LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams margins(LinearLayout.LayoutParams params, float left, float top, float right, float bottom) {
        params.setMargins(dp(left), dp(top), dp(right), dp(bottom));
        return params;
    }
}
