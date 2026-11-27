import gettext
import math
import time

from neomixer.clients.gtk.widgets.eq.eq_curve import format_freq
from neomixer.clients.gtk.widgets.eq.effects_window import EFFECTS, EFFECT_COLORS
from neomixer.scripts.pw_eq import DEESSER_SPLIT
from neomixer.scripts.pw_fxmeter import EffectsMeter
from neomixer.utils.eq_math import deesser_db, response_db
from neomixer.utils.spectrum import MIN_FREQ, MAX_FREQ, DB_FLOOR, SpectrumAnalyzer, band_edges

# pylint: disable=wrong-import-order,wrong-import-position
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib  # noqa: E402
# pylint: enable=wrong-import-order,wrong-import-position

_ = gettext.gettext

TOP_DB = 0.0
BOTTOM_DB = -90.0
GRID_FREQS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
GRID_DBS = (0, -12, -24, -36, -48, -60, -72, -84)
FALL_DB_PER_SECOND = 40.0
HOLD_SECONDS = 1.5
HOLD_FALL_DB_PER_SECOND = 20.0
LEFT, RIGHT, TOP, BOTTOM = 40, 12, 10, 22
LABEL_SIZE = 10
BAR_COLOR = (0.22, 0.74, 0.97)
LOUD_COLOR = (0.98, 0.75, 0.14)
CLIP_COLOR = (0.94, 0.27, 0.27)
EQ_COLOR = (0.98, 0.75, 0.14)
NOW_COLOR = (0.93, 0.95, 1.0)
# The effects panel: what the EQ and the effects do to each frequency
GAIN_RANGE = 24.0
GAIN_GRID = (-24, -12, 0, 12, 24)
# How often the meter is checked for a chain that started or was rebuilt, and how long
# without a report means the chain stopped
METER_CHECK_MS = 1000
LIVE_TIMEOUT = 0.6


class SpectrumView(Gtk.DrawingArea):
    '''Bars of dBFS per sixth of an octave, falling slowly, with a peak line that holds.'''

    def __init__(self, **kwargs):
        super().__init__(content_width=640, content_height=280, hexpand=True, vexpand=True, **kwargs)
        self.bands = band_edges()
        self.levels = [DB_FLOOR] * len(self.bands)
        self.holds = [DB_FLOOR] * len(self.bands)
        self._hold_until = [0.0] * len(self.bands)
        self._last = time.monotonic()
        self.text_factor = 1.0
        self.caption = ''
        self.set_draw_func(self._draw)

    def set_text_factor(self, factor):
        self.text_factor = factor
        self.queue_draw()

    def set_caption(self, caption):
        self.caption = caption
        self.queue_draw()

    def push(self, levels):
        now = time.monotonic()
        elapsed, self._last = min(now - self._last, 0.5), now
        for index, level in enumerate(levels):
            self.levels[index] = max(level, self.levels[index] - FALL_DB_PER_SECOND * elapsed)
            if level >= self.holds[index]:
                self.holds[index] = level
                self._hold_until[index] = now + HOLD_SECONDS
            elif now > self._hold_until[index]:
                self.holds[index] = max(level, self.holds[index] - HOLD_FALL_DB_PER_SECOND * elapsed)
        self.queue_draw()

    @property
    def _left(self):
        return LEFT * self.text_factor

    @property
    def _bottom(self):
        return BOTTOM * self.text_factor

    def _freq_to_x(self, freq, width):
        span = math.log10(MAX_FREQ / MIN_FREQ)
        return self._left + (width - self._left - RIGHT) * math.log10(freq / MIN_FREQ) / span

    def _db_to_y(self, db, height):
        db = min(max(db, BOTTOM_DB), TOP_DB)
        return TOP + (height - TOP - self._bottom) * (TOP_DB - db) / (TOP_DB - BOTTOM_DB)

    def _draw(self, _area, cr, width, height):
        self._draw_grid(cr, width, height)
        floor_y = self._db_to_y(BOTTOM_DB, height)

        for (low, _center, high), level, hold in zip(self.bands, self.levels, self.holds):
            x0 = self._freq_to_x(max(low, MIN_FREQ), width) + 1
            x1 = self._freq_to_x(min(high, MAX_FREQ), width) - 1
            if x1 <= x0:
                continue
            color = CLIP_COLOR if level > -1 else LOUD_COLOR if level > -12 else BAR_COLOR
            if level > BOTTOM_DB:
                y = self._db_to_y(level, height)
                cr.set_source_rgba(*color, 0.85)
                cr.rectangle(x0, y, x1 - x0, floor_y - y)
                cr.fill()
            if hold > BOTTOM_DB:
                cr.set_source_rgba(1, 1, 1, 0.75)
                cr.rectangle(x0, round(self._db_to_y(hold, height)) - 1, x1 - x0, 2)
                cr.fill()

        if self.caption:
            cr.set_font_size(LABEL_SIZE * self.text_factor * 1.05)
            extents = cr.text_extents(self.caption)
            cr.set_source_rgba(1, 1, 1, 0.6)
            cr.move_to(width - RIGHT - extents.x_advance - 8, TOP + extents.height + 8)
            cr.show_text(self.caption)

    def _draw_grid(self, cr, width, height):
        cr.set_source_rgb(0.055, 0.067, 0.09)
        cr.paint()
        cr.set_font_size(LABEL_SIZE * self.text_factor)
        cr.set_line_width(1)

        for freq in GRID_FREQS:
            x = self._freq_to_x(freq, width)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.move_to(round(x) + 0.5, TOP)
            cr.line_to(round(x) + 0.5, height - self._bottom)
            cr.stroke()
            label = format_freq(freq)
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(min(max(x - extents.width / 2, 0), width - extents.width - 1), height - 7 * self.text_factor)
            cr.show_text(label)

        for db in GRID_DBS:
            y = self._db_to_y(db, height)
            cr.set_source_rgba(1, 1, 1, 0.2 if db == 0 else 0.07)
            cr.move_to(self._left, round(y) + 0.5)
            cr.line_to(width - RIGHT, round(y) + 0.5)
            cr.stroke()
            label = str(db)
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(self._left - extents.width - 6, y + extents.height / 2)
            cr.show_text(label)


class EffectsView(Gtk.DrawingArea):
    '''
    What the EQ and the effects of a device do right now, per frequency, under its spectrum
    and on the same frequency axis: each effect as a dashed line in its color, the EQ in
    yellow, and all of it together in white.
    '''

    def __init__(self, device_model, **kwargs):
        super().__init__(content_width=640, content_height=170, hexpand=True, **kwargs)
        self.device_model = device_model
        self.live = {}
        self.text_factor = 1.0
        self.set_draw_func(self._draw)

    def set_text_factor(self, factor):
        self.text_factor = factor
        self.queue_draw()

    def set_live(self, live):
        self.live = live
        self.queue_draw()

    @property
    def _left(self):
        return LEFT * self.text_factor

    @property
    def _bottom(self):
        return BOTTOM * self.text_factor

    def _freq_to_x(self, freq, width):
        span = math.log10(MAX_FREQ / MIN_FREQ)
        return self._left + (width - self._left - RIGHT) * math.log10(freq / MIN_FREQ) / span

    def _gain_to_y(self, gain, height):
        gain = min(max(gain, -GAIN_RANGE), GAIN_RANGE)
        return TOP + (height - TOP - self._bottom) * (GAIN_RANGE - gain) / (2 * GAIN_RANGE)

    def lines(self):
        '''(label, color, gain in dB, de-esser split or None) of each effect that is on, in their order.'''
        effects = self.device_model.effects
        if effects.bypass:
            return []
        result = []
        for key, title, _model, _text, _params in EFFECTS:
            if key == 'eq' or not getattr(effects, key).enabled:
                continue
            split = effects.deesser.freq * DEESSER_SPLIT if key == 'deesser' else None
            result.append((title, EFFECT_COLORS[key], self.live.get(key, 0.0), split))
        return result

    def _trace(self, cr, freqs, gains, width, height):
        for index, (freq, gain) in enumerate(zip(freqs, gains)):
            point = (self._freq_to_x(freq, width), self._gain_to_y(gain, height))
            if index == 0:
                cr.move_to(*point)
            else:
                cr.line_to(*point)

    def _draw(self, _area, cr, width, height):
        self._draw_grid(cr, width, height)
        points = max(min(int(width - self._left - RIGHT) // 3, 260), 2)
        freqs = [MIN_FREQ * (MAX_FREQ / MIN_FREQ) ** (index / (points - 1)) for index in range(points)]

        eq = self.device_model.eq
        eq_on = eq.enabled and not self.device_model.effects.bypass
        total = [response_db(eq, freq) for freq in freqs] if eq_on else [0.0] * len(freqs)
        if eq_on:
            cr.set_source_rgba(*EQ_COLOR, 0.8)
            cr.set_line_width(1.4)
            self._trace(cr, freqs, total, width, height)
            cr.stroke()

        lines = []
        for label, color, gain, split in self.lines():
            gains = [gain] * len(freqs) if split is None else [deesser_db(gain, split, freq) for freq in freqs]
            total = [a + b for a, b in zip(total, gains)]
            lines.append((color, gains))

        cr.set_source_rgba(*NOW_COLOR, 0.92)
        cr.set_line_width(2.0)
        self._trace(cr, freqs, total, width, height)
        cr.stroke()

        # Dashed on top, so one that does all there is to see stays visible on the white
        cr.set_line_width(1.6)
        cr.set_dash([6, 4])
        for color, gains in lines:
            cr.set_source_rgb(*color)
            self._trace(cr, freqs, gains, width, height)
            cr.stroke()
        cr.set_dash([])
        self._draw_legend(cr, eq_on)

    def _draw_legend(self, cr, eq_on):
        size = LABEL_SIZE * self.text_factor
        cr.set_font_size(size * 1.05)
        line = size * 1.55
        entries = [(_('EQ'), EQ_COLOR, None)] if eq_on else []
        entries += [(label, color, gain) for label, color, gain, _split in self.lines()]
        entries.append((_('All together, now'), NOW_COLOR, None))
        x, y = self._left + 10, TOP + line
        box_width = max(cr.text_extents(f'{label}  -00.0 dB').x_advance for label, _c, _g in entries) + size * 2
        cr.set_source_rgba(0.055, 0.067, 0.09, 0.82)
        cr.rectangle(x - 6, y - line + 2, box_width, line * len(entries) + 4)
        cr.fill()
        for label, color, gain in entries:
            cr.set_source_rgb(*color)
            cr.rectangle(x, y - size * 0.75, size * 0.8, size * 0.8)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.move_to(x + size * 1.3, y)
            cr.show_text(label if gain is None else f'{label}  {gain:+.1f} dB')
            y += line

    def _draw_grid(self, cr, width, height):
        cr.set_source_rgb(0.055, 0.067, 0.09)
        cr.paint()
        cr.set_font_size(LABEL_SIZE * self.text_factor)
        cr.set_line_width(1)
        for freq in GRID_FREQS:
            x = self._freq_to_x(freq, width)
            cr.set_source_rgba(1, 1, 1, 0.07)
            cr.move_to(round(x) + 0.5, TOP)
            cr.line_to(round(x) + 0.5, height - self._bottom)
            cr.stroke()
            label = format_freq(freq)
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(min(max(x - extents.width / 2, 0), width - extents.width - 1), height - 7 * self.text_factor)
            cr.show_text(label)
        for gain in GAIN_GRID:
            y = self._gain_to_y(gain, height)
            cr.set_source_rgba(1, 1, 1, 0.2 if gain == 0 else 0.07)
            cr.move_to(self._left, round(y) + 0.5)
            cr.line_to(width - RIGHT, round(y) + 0.5)
            cr.stroke()
            label = f'{gain:+d}' if gain else '0 dB'
            extents = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.45)
            cr.move_to(self._left - extents.width - 6, y + extents.height / 2)
            cr.show_text(label)


class SpectrumWindow(Gtk.Window):
    '''
    Live spectrum of one device. It records only while open: closing the window ends the
    recording and destroys the window.
    '''

    def __init__(self, device_model, source, meter=None, **kwargs):
        super().__init__(title=_('Spectrum: %s') % device_model.nick, default_width=760, default_height=360, **kwargs)
        self.add_css_class('neomixer-eq')
        self.device_model = device_model
        self.view = SpectrumView()
        self.effects_view = EffectsView(device_model)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.view)
        box.append(Gtk.Separator())
        box.append(self.effects_view)
        self.set_child(box)
        self._latest = None
        # What the effects do right now, see scripts.pw_fxmeter
        self.live = {}
        self.live_time = 0.0
        self.effects_meter = EffectsMeter(device_model.name, lambda levels: GLib.idle_add(self._on_effects, levels))
        self._meter_timer = None
        self.effects_changed()
        self._queued = False
        # the strip's meter knows what the fader and mute do to the recorded level
        self.meter = meter
        self.analyzer = SpectrumAnalyzer(source, self._on_levels, self.view.bands)
        self.connect('close-request', self._on_close)

    def set_text_factor(self, factor):
        self.view.set_text_factor(factor)
        self.effects_view.set_text_factor(factor)

    def start(self):
        self._meter_timer = GLib.timeout_add(METER_CHECK_MS, self._check_meter)
        self.effects_meter.schedule(self.device_model.eq, self.device_model.effects, self.device_model.name)
        return self.analyzer.start()

    def effects_changed(self):
        '''
        The device's EQ or effects changed: the panel shows while there is any, and the meter
        follows. The default size grows with it, a size the user chose stays.
        '''
        effects = self.device_model.effects
        used = (self.device_model.eq.enabled or bool(effects.enabled_names())) and not effects.bypass
        if used != self.effects_view.get_visible():
            self.effects_view.set_visible(used)
            self.set_default_size(760, 540 if used else 360)
        # An output's monitor plays what its effects put out, an input is recorded before its own
        after = self.device_model.get_type() in ('a', 'b')
        self.view.set_caption((_('After the effects') if after else _('Before the effects')) if used else '')
        self.effects_view.queue_draw()
        if self._meter_timer is not None:
            self.effects_meter.schedule(self.device_model.eq, effects, self.device_model.name)

    def _check_meter(self):
        '''Follows a chain that started, stopped or was rebuilt.'''
        self.effects_meter.schedule(self.device_model.eq, self.device_model.effects, self.device_model.name)
        if self.live and time.monotonic() - self.live_time > LIVE_TIMEOUT:
            self._show_effects({})
        return GLib.SOURCE_CONTINUE

    def _on_effects(self, levels):
        self.live_time = time.monotonic()
        self._show_effects(levels)
        return GLib.SOURCE_REMOVE

    def _show_effects(self, levels):
        self.live = levels
        self.effects_view.set_live(levels)

    def _on_levels(self, levels):
        '''Runs on the recording thread. Only the newest frame gets drawn.'''
        self._latest = levels
        if not self._queued:
            self._queued = True
            GLib.idle_add(self._draw_latest)

    def _draw_latest(self):
        self._queued = False
        if self._latest is not None:
            gain = self.meter.gain if self.meter is not None else 1.0
            offset = 20 * math.log10(gain) if gain > 0 else None
            self.view.push([DB_FLOOR if offset is None else max(DB_FLOOR, level + offset) for level in self._latest])
        return GLib.SOURCE_REMOVE

    def _on_close(self, *_):
        if self._meter_timer is not None:
            GLib.source_remove(self._meter_timer)
            self._meter_timer = None
        self.effects_meter.stop_soon()
        self.analyzer.stop()
        return False
