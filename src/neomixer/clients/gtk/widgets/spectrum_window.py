import gettext
import math
import time

from neomixer.clients.gtk.widgets.eq.eq_curve import format_freq
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
        self.set_draw_func(self._draw)

    def set_text_factor(self, factor):
        self.text_factor = factor
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


class SpectrumWindow(Gtk.Window):
    '''
    Live spectrum of one device. It records only while open: closing the window ends the
    recording and destroys the window.
    '''

    def __init__(self, device_model, source, meter=None, **kwargs):
        super().__init__(title=_('Spectrum: %s') % device_model.nick, default_width=760, default_height=360, **kwargs)
        self.add_css_class('neomixer-eq')
        self.view = SpectrumView()
        self.set_child(self.view)
        self._latest = None
        self._queued = False
        # the strip's meter knows what the fader and mute do to the recorded level
        self.meter = meter
        self.analyzer = SpectrumAnalyzer(source, self._on_levels, self.view.bands)
        self.connect('close-request', self._on_close)

    def set_text_factor(self, factor):
        self.view.set_text_factor(factor)

    def start(self):
        return self.analyzer.start()

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
        self.analyzer.stop()
        return False
